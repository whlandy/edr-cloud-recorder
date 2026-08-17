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
import time
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



def _first_visible(locator):
    """返回第一个可见的匹配元素；没有就返回 None。

    不能直接 .first —— 隐藏的诱饵元素往往排在真表单前面，
    .first 会稳定地选中那个错的。
    """
    try:
        total = locator.count()
    except Exception:
        return None
    for index in range(min(total, 8)):
        candidate = locator.nth(index)
        try:
            if candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


def _visible_login_form(page: Page, *, timeout_ms: int = 30_000):
    """在所有 frame 里找可见的登录表单，返回 (用户名框, 密码框, 所在 frame)。

    以**密码框可见**为锚：一个页面上可能有好几个文本框（搜索、语言切换），
    但可见的密码框基本只有登录表单里那一个。找到它之后，用户名框就在同一个
    frame 里取第一个可见的文本输入框。

    **把 frame 一起返回**：提交按钮也在同一个 frame 里。只返回输入框的话，
    调用方会习惯性地写 page.get_by_role("button", ...) 去点登录 ——
    那又回到主文档，于是表单填好了却点不到按钮，报错还只说「按钮超时」。
    实测就是这么栽的第二次。

    找不到时把每个 frame 的情况一并报出来 —— 否则只说「超时」，
    排查的人不知道该去看主文档还是某个 iframe。
    """
    deadline = time.monotonic() + timeout_ms / 1000
    seen: list[str] = []
    while True:
        seen = []
        for frame in page.frames:
            pass_box = _first_visible(frame.locator("input[type=password]"))
            if pass_box is None:
                seen.append(f"{frame.url[:60] or '(主文档)'}: 无可见密码框")
                continue
            # 用户名框取**密码框之前最近的那个**，不是 frame 里的第一个。
            # 按顺序取会踩到排在表单上方的搜索框 / 租户框：用户名被填进去，
            # 而紧随其后的 to_have_value 断言恰好通过（填的就是它），
            # 故障要等到 poll_until(logged_in) 超时才以无关的理由暴露。
            user_box = _first_visible(pass_box.locator(
                "xpath=preceding::input["
                "@type='text' or @type='email' or not(@type)][1]"
            )) or _first_visible(
                frame.locator("input[type=text], input[type=email], input:not([type])")
            )
            if user_box is not None:
                return user_box, pass_box, frame
            seen.append(f"{frame.url[:60] or '(主文档)'}: 有密码框但没有可见的用户名框")
        if time.monotonic() >= deadline:
            detail = "\n  ".join(seen) or "(页面里一个 frame 都没有)"
            raise TimeoutError(
                "找不到可见的登录表单。各 frame 的情况：\n  " + detail +
                "\n如果这个站点的登录方式特殊（验证码、扫码、多步），"
                "改用 manual_login.py 人工登录一次并导出登录态。"
            )
        page.wait_for_timeout(250)



def _fill_credentials(user_box, pass_box, user: str, attempts: int = 3) -> None:
    """把用户名和密码填进各自的框，填错就重来。

    这个登录页有个**偶发**故障：填完密码之后，用户名框里会变成「用户名+密码」
    拼在一起（组件把两次输入并进了同一个 model，title 属性上看得最清楚）。
    根因未查明 —— 已排除的方向见下，别重复走：

      - 不是 fill() 在受控组件上失效：单独测，值读得回来
      - 不是两个 fill 背靠背的竞态：照抄同样时序连跑 6 次，全对
      - 不是入口 URL 不同导致的 iframe 重载：从 / 进也连跑 6 次，全对

    所以这里不假装修好了它，只做三件让它可控的事：
      1. 每步单独校验，故障早暴露，且用户名那步与密码无关
      2. 校验只比长度，**不打印内容** —— 旧写法 to_have_value 会把
         「用户名+密码」整串打进终端和 error-context.md，那正是密码泄露的路径
      3. 发现污染就清空重来，并大声记录；重来仍失败才抛

    重来时**两个框都清**：污染意味着密码可能压根没进密码框，只补用户名会留下
    一个填了一半的表单，随后以「登录失败」这种无关的理由收场。
    """
    for attempt in range(1, attempts + 1):
        user_box.fill("")
        pass_box.fill("")
        user_box.fill(user)
        # 密码不绑定到局部变量，避免进 --showlocals 的输出。
        # credentials() 返回的是临时 dict，不是具名局部变量，取完即弃。
        pass_box.fill(credentials()["password"])

        polluted = len(user_box.input_value()) != len(user)
        empty_pass = len(pass_box.input_value()) != len(credentials()["password"])
        if not polluted and not empty_pass:
            return
        print(
            f"⚠ 第 {attempt} 次填写异常（用户名框被污染={polluted}、密码框长度不符={empty_pass}），"
            "重填。这是已知偶发，根因未查明。"
        )
    raise RuntimeError(
        f"连续 {attempts} 次都没能把凭据正确填进登录表单。"
        "为避免泄露，这里不打印框里的实际内容；"
        "改用 python manual_login.py 人工登录一次并导出登录态。"
    )


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

    # 找到真正能输入的那个表单。不要写死 page.locator("#username")：
    #
    #   - 登录表单常在 **iframe** 里（SSO 尤其如此），主文档搜不到；
    #   - 主文档上又常有同 id / 同 placeholder 的**隐藏诱饵**（自动填充陷阱）。
    #
    # 实测踩过：某站点主文档有个隐藏的 #username（name="ssoCredentials.username"），
    # 可见的表单在 iframe 里 —— 于是 wait_for(visible) 一直等到超时，
    # 而报错只说「元素不可见」，看不出真正的表单在别处。
    #
    # 判据是「密码框可见」：可见的那个才是人正在用的那个。
    user_box, pass_box, form = _visible_login_form(page, timeout_ms=30_000)

    _fill_credentials(user_box, pass_box, user)

    # 在**表单所在的 frame** 里找提交按钮，不是 page —— 见 _visible_login_form 的说明。
    # 有些登录页的提交是 <a> 或 <div>，所以 role=button 找不到时按文本兜底。
    # 兜底**只在可点控件里**按文本找。用 get_by_text 会匹配任意含该文本的元素，
    # 而登录页的 <h2>登录</h2> 通常排在按钮前面 —— 点中标题，表单从未提交，
    # 最后以 poll_until(logged_in) 超时收场，报错和真实原因毫无关系。
    submit = re.compile(r"登录|登入|Sign in|Log in", re.I)
    clickable = "button, a, [role=button], input[type=submit], input[type=button]"
    button = _first_visible(form.get_by_role("button", name=submit)) \
        or _first_visible(form.locator("input[type=submit]")) \
        or _first_visible(form.locator(clickable).filter(has_text=submit))
    if button is None:
        raise TimeoutError("表单找到了，但同一个 frame 里没有可见的提交按钮")
    button.click()

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
