#!/usr/bin/env python3
"""手动登录并导出登录态（manual-login.mjs 的 Python 版）。

站点在风控触发后会要求滑块验证码，自动登录就走不通了。验证码本来就是用来
拦自动化的，不该去绕；正确做法是让人过一次，然后复用会话 —— 登录态能用一段时间，
期间所有云端操作都不必再登录。

用法：python manual_login.py
  浏览器窗口打开 → 你手动登录（含验证码）→ 检测到登录成功后自动导出并关闭
"""

import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auth_setup import dismiss_dialogs, export_state  # noqa: E402
from chrome_path import resolve_chrome                # noqa: E402

HERE = Path(__file__).resolve().parent
AUTH = HERE / ".auth"
WAIT_MINUTES = 15

BASE = os.environ.get("REC_BASE_URL")
if not BASE:
    sys.exit("请设置 REC_BASE_URL")
ENTRY = BASE.rstrip("/") + os.environ.get("REC_ENTRY_PATH", "/")

# 自检用的只读接口，按站点改；留空则只探首页
PROBE = os.environ.get("REC_PROBE_PATH", "/")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            executable_path=resolve_chrome(),
            args=["--ignore-certificate-errors", "--start-maximized"],
        )
        # viewport=None 让页面跟着真实窗口尺寸走 —— 手动操作时锁死 viewport
        # 会把底部的登录按钮挤到可视区外
        ctx = browser.new_context(ignore_https_errors=True, no_viewport=True)
        page = ctx.new_page()
        page.set_default_navigation_timeout(90_000)

        print(f"\n浏览器已打开：{ENTRY}")
        print("请在窗口里登录（含滑块验证码）。检测到登录成功后会自动导出并关闭窗口。")
        print(f"最多等待 {WAIT_MINUTES} 分钟。\n")

        try:
            page.goto(ENTRY, wait_until="domcontentloaded")
        except Exception:
            pass

        # 判据用应用侧状态而不是 URL —— SSO 回调会多跳几次，参数还不固定。
        # try/except 是必须的：轮询期间撞上导航，evaluate 会抛
        # Execution context was destroyed。
        deadline = time.monotonic() + WAIT_MINUTES * 60
        ok = False
        while time.monotonic() < deadline:
            try:
                ok = bool(page.evaluate(
                    "() => sessionStorage.getItem('userInfo') !== null"))
            except Exception:
                ok = False
            if ok:
                break
            page.wait_for_timeout(1500)

        if not ok:
            print(f"\n❌ {WAIT_MINUTES} 分钟内未检测到登录成功，未导出任何内容。")
            browser.close()
            return 1

        dismiss_dialogs(page)
        page.wait_for_timeout(1500)

        export_state(ctx, page, AUTH)

        # 立刻验证导出的凭据真能调通接口。只看到"登录成功"是不够的 ——
        # 否则后面每个用例都会因为同一个原因失败，而失败点散落各处。
        probe = page.evaluate(
            """async (u) => {
                 const r = await fetch(u);
                 return { status: r.status,
                          json: (r.headers.get('content-type') ?? '').includes('json') };
               }""",
            PROBE,
        )
        print(f"\n✅ 登录态已导出到 {AUTH}/")
        print(f"   连通性自检：HTTP {probe['status']}，返回 JSON = {probe['json']}")
        if not probe["json"]:
            print("   ⚠ 返回的不是 JSON，会话可能仍有问题")

        browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
