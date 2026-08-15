"""配置加载（config.mjs 的 Python 版）。

查找顺序：
  1. --config 指定的路径
  2. 与本文件同目录的 config.json（项目内覆盖，便于一个工作区一套参数）
  3. ./config.json（当前工作目录覆盖）
  4. ~/.config/edr-cloud-recorder/config.json（用户级默认，遵循 XDG）

凭据默认放在用户级目录而不是项目目录，因为项目目录常常就是仓库目录 ——
.gitignore 挡不住 git add -A，而放在 ~/.config 下根本不会被任何仓库看见。
也可以留空，回退到环境变量 REC_USER / REC_PASSWORD。

文件名叫 rec_config 而不是 config：scripts/ 会被加进 sys.path，
一个叫 config 的模块太容易和用户项目里的同名模块撞车。
"""

import json
import os
import stat
from pathlib import Path

HERE = Path(__file__).resolve().parent
USER_CONFIG = Path(
    os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
) / "edr-cloud-recorder" / "config.json"


class ConfigError(Exception):
    pass


def load_config(explicit: str | None = None) -> dict:
    path: Path | None = None
    if explicit:
        path = Path(explicit).resolve()
        if not path.exists():
            raise ConfigError(f"--config 指定的文件不存在：{path}")
    else:
        for cand in (HERE / "config.json", Path("config.json").resolve(), USER_CONFIG):
            if cand.exists():
                path = cand
                break

    if path is None:
        return {"_path": None}

    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ConfigError(f"config.json 解析失败（{path}）：{e}") from None

    # 只在真的存了密码时才检查权限 —— 没有秘密就不必打扰用户
    if (cfg.get("auth") or {}).get("password"):
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            print(f"⚠ {path} 存有密码但权限过宽，建议：chmod 600 {path}")

    cfg["_path"] = str(path)
    return cfg


def resolve_auth(cfg: dict | None = None) -> dict:
    """凭据：环境变量优先，回退配置文件。返回的对象不要打印。

    cfg 传 None 时自己去找配置文件（./config.json → ~/.config/...）。
    由 assets/auth_setup.py 的 require_credentials() 调用。

    环境变量优先于配置文件：配置是「这台机器上的默认值」，env 是「这一次的覆盖」。
    """
    if cfg is None:
        try:
            cfg = load_config(None)
        except ConfigError:
            cfg = {}
    auth = cfg.get("auth") or {}
    return {
        "user": os.environ.get("REC_USER") or auth.get("user") or "",
        "password": os.environ.get("REC_PASSWORD") or auth.get("password") or "",
    }


def with_defaults(cfg: dict, *, url=None, api=None, out=None) -> dict:
    """配置里的值作为默认，命令行参数优先级更高。"""
    base = cfg.get("baseUrl")
    if base and cfg.get("entryPath"):
        base = base.rstrip("/") + cfg["entryPath"]
    record = cfg.get("record") or {}
    browser = cfg.get("browser") or {}
    return {
        "url": url or os.environ.get("REC_URL") or base,
        "api_filter": api or record.get("apiFilter") or None,
        "out_dir": out or record.get("outDir") or "recordings",
        "chrome_bin": os.environ.get("REC_CHROME_BIN") or browser.get("executablePath") or None,
    }
