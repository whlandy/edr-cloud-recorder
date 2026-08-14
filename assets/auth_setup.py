"""登录一次，把登录态导出给后续所有用例复用（auth.setup.ts 的 Python 版）。

关键点：Playwright 的 storage_state **只保存 cookies + localStorage**，
不保存 sessionStorage。如果目标站点把 token 放在 sessionStorage 里
（表现为：新标签页打开需要重新登录），标准的「登录一次复用 storage_state」
方案会失败 —— 应用启动时读不到 token，立刻跳登录页。

所以这里额外导出 sessionStorage，由 conftest.py 的 authed_page 用
add_init_script 注回去。

── 使用前需要改的地方 ──────────────────────────────
  1. ENTRY_URL / 登录成功的判据（logged_in）
  2. 用户名、密码输入框的定位方式（login 里的 user_box / pass_box）
  3. 一次性弹窗的关闭逻辑（如果有）

── 凭据从哪来 ─────────────────────────────────
环境变量 REC_USER / REC_PASSWORD 优先，回退 config.json 的 auth 段
（查找顺序见 rec_config.py）。配置是「这台机器上的默认值」，env 是「这一次的覆盖」。

真要把密码落盘，放 ~/.config/edr-cloud-recorder/config.json 并 chmod 600 ——
别放项目目录，那通常就是仓库目录，.gitignore 挡不住 git add -A。

── 与 JS 版的一处安全差异 ─────────────────────────
JS 版把密码读进局部变量 pass。Python 侧不这么做：pytest 的 --showlocals / -l
会把栈帧里的局部变量原样打印出来，密码就跟着进了终端和 CI 日志。
这里让密码只以 credentials()["password"] 的形式出现在调用点，不绑定到任何名字；
require_credentials() 里那个 auth 在判完之后立刻 del 掉 —— 否则「只设了密码、
忘了用户名」这一种情形会带着真密码抛异常。
"""

import json
import os
import re
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect

from rec_assert import poll_until
from rec_config import resolve_auth

ENTRY_PATH = os.environ.get("REC_ENTRY_PATH", "/")


def credentials() -> dict:
    """取凭据：环境变量优先，回退 config.json 的 auth 段。

    返回 {"user": ..., "password": ...}。**不要打印这个对象。**
    """
    return resolve_auth()


def require_credentials() -> str:
    """确认凭据齐备，返回用户名。密码不在这里返回 —— 见 login() 里的说明。"""
    auth = credentials()
    user, ok = auth["user"], bool(auth["user"] and auth["password"])
    # 把密码从这一帧的局部变量里去掉：只设了密码没设用户名时下面会抛异常，
    # 而 --showlocals 会把栈上的 auth 整个打出来
    del auth
    if not ok:
        raise RuntimeError(
            "缺少凭据。两种给法，二选一：\n"
            "  1) 环境变量（推荐，不落盘）：\n"
            "       export REC_USER=...\n"
            "       export REC_PASSWORD=...\n"
            "  2) 配置文件的 auth 段：\n"
            "       ~/.config/edr-cloud-recorder/config.json   （chmod 600）\n"
            "       别放项目目录 —— 那通常就是仓库目录，.gitignore 挡不住 git add -A\n"
            "\n"
            "如果目标站点有滑块验证码，自动登录走不通，改用手动登录：\n"
            "  python manual_login.py"
        )
    return user


def logged_in(page: Page) -> bool:
    """── 改这里：换成可靠的登录成功判据 ──

    不要只判断 URL —— SSO 回调可能多跳几次，参数也不固定，判断应用侧状态更准确。

    try/except 不能省：SSO 登录后页面会连续跳转，轮询期间 page.evaluate
    撞上导航会抛 "Execution context was destroyed"。把异常当作"还没就绪"，
    轮询才真正起作用 —— 否则第一次撞上导航就直接失败了。
    """
    try:
        return bool(page.evaluate(
            "() => sessionStorage.length > 0 || localStorage.getItem('token') !== null"
        ))
    except Exception:
        return False


def dismiss_dialogs(page: Page) -> None:
    """关掉登录后的一次性弹窗（首次引导、公告、协议）。

    统一在这里关，别留给每个用例各自处理。
    """
    dlg = page.get_by_role("dialog")
    try:
        if dlg.is_visible():
            dlg.get_by_role(
                "button", name=re.compile(r"关闭|我知道了|确定|×")
            ).first.click()
    except Exception:
        pass


def login(page: Page) -> None:
    """走一遍登录流程。跑完时页面应已处于登录态。"""
    user = require_credentials()

    page.goto(ENTRY_PATH)

    # 未登录时通常会被重定向到登录页。用 URL 特征判断比固定等待可靠。
    try:
        page.wait_for_url(re.compile("login", re.I), timeout=60_000)
    except Exception:
        pass  # 已经是登录态就不会跳转，继续往下走

    # Cookie 同意横幅之类的东西会挡住输入框
    consent = page.get_by_role(
        "button", name=re.compile(r"接受|同意|关闭|Accept", re.I)
    ).first
    try:
        if consent.is_visible():
            consent.click()
    except Exception:
        pass

    # ── 改这里：换成实际的输入框定位 ──
    #
    # 用稳定的 id 或 label，**不要**用 .or_() 组合多种定位方式。
    # 踩过的坑：某些登录页的密码框没有 placeholder（可访问名来自旁边的图标或标签），
    # get_by_placeholder(/密码/).or_("#password") 会解析到用户名框，
    # 结果用户名和密码被填进同一个输入框 —— 而失败现场里那串明文就是泄露源。
    user_box = page.locator("#username")
    pass_box = page.locator("#password")

    user_box.wait_for(state="visible", timeout=30_000)
    pass_box.wait_for(state="visible", timeout=30_000)

    user_box.fill(user)
    # 密码不绑定到局部变量，避免进 --showlocals 的输出。
    # credentials() 返回的 dict 是临时对象，不是具名局部变量，取完即弃。
    pass_box.fill(credentials()["password"])
    # 填完校验一次：填错框是静默失败，只有断言能把它变成显式失败
    expect(user_box, "用户名框内容异常，疑似把密码也填了进去").to_have_value(user)

    page.get_by_role("button", name=re.compile(r"登录|登入|Sign in", re.I)).click()

    poll_until(
        lambda: logged_in(page), True,
        timeout=60.0, interval=1.0,
    )

    dismiss_dialogs(page)


def export_state(context: BrowserContext, page: Page, auth_dir: Path) -> None:
    """把登录态落盘：storage_state + sessionStorage + 纯 cookie 导出。"""
    auth_dir.mkdir(parents=True, exist_ok=True)

    # 1) cookies + localStorage
    context.storage_state(path=str(auth_dir / "state.json"))

    # 2) sessionStorage —— Playwright 不管这块，必须自己存
    (auth_dir / "session-storage.json").write_text(
        page.evaluate("() => JSON.stringify(sessionStorage)"), encoding="utf-8"
    )

    # 3) 纯 cookie 导出，方便 curl / requests 等复用
    (auth_dir / "cookies.json").write_text(
        json.dumps(context.cookies(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"登录态已导出到 {auth_dir}/（记得加进 .gitignore）")
