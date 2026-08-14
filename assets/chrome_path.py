"""复用本机已有的 Chromium 构建。

Playwright 每个版本只认自己那一版 browser build，升级后会要求重下约 170MB。
内网/弱网环境里这一步经常卡死。缓存里通常已有可用的构建，直接指过去即可。

JS 侧这套逻辑写了两份（scripts/chrome-path.mjs 给录制器，playwright.config.ts
给回放），因为一边是 ESM 一边是 TS。Python 侧两边都是 Python，所以只留这一份。
"""

import os
import re
import sys
from pathlib import Path

_RELS = {
    "darwin": [
        "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
    ],
    "win32": ["chrome-win/chrome.exe", "chrome-win64/chrome.exe"],
}
_RELS_DEFAULT = ["chrome-linux/chrome", "chrome-linux64/chrome"]

_SYSTEM = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ],
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
}
_SYSTEM_DEFAULT = ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"]


def _cache_dir() -> Path | None:
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env:
        return Path(env)
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Caches/ms-playwright"
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return Path(local) / "ms-playwright" if local else None
    return home / ".cache/ms-playwright"


def resolve_chrome() -> str | None:
    """按 环境变量 → Playwright 缓存（版本号大的优先）→ 系统安装 的顺序找。

    返回 None 表示交给 Playwright 自己决定 —— 不是错误。
    """
    env = os.environ.get("REC_CHROME_BIN")
    if env:
        return env

    rels = _RELS.get(sys.platform, _RELS_DEFAULT)
    cache = _cache_dir()
    if cache and cache.is_dir():
        builds = sorted(
            (d for d in cache.iterdir() if re.fullmatch(r"chromium-\d+", d.name)),
            key=lambda d: int(d.name.split("-")[1]),
            reverse=True,
        )
        for b in builds:
            for r in rels:
                p = b / r
                if p.exists():
                    return str(p)

    for p in _SYSTEM.get(sys.platform, _SYSTEM_DEFAULT):
        if Path(p).exists():
            return p
    return None


if __name__ == "__main__":
    p = resolve_chrome()
    print(p if p else "未找到可复用的构建，将交给 Playwright 自行决定")
