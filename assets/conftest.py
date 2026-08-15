"""回放工程的配置与 fixture（playwright.config.ts + fixtures.ts 的 Python 版）。

JS 侧的结构靠 config 里的 projects 表达：一个 setup project 先跑登录，
其余 project 用 dependencies 依赖它。pytest 没有 project 概念，等价物是
**session 作用域的 fixture** —— 这份文件就是那个映射：

    projects: [{name:'setup'}]        → auth_state（session fixture，只跑一次）
    dependencies: ['setup']           → browser_context_args 依赖 auth_state
    use.storageState                  → browser_context_args["storage_state"]
    use.launchOptions.executablePath  → browser_type_launch_args
    use.baseURL                       → base_url
    use.trace/video/screenshot        → 命令行 --tracing/--video/--screenshot（见 pytest.ini）
    use.actionTimeout/navigationTimeout → _apply_timeouts（autouse）
    expect.timeout                    → expect.set_options
    globalTeardown: scrub-auth-artifacts → _scrub_auth_artifacts（session 收尾）
    workers: 1                        → pytest 默认串行，不用配
    retries                           → pytest-rerunfailures 的 --reruns（见 pytest.ini）

── 一个比 JS 版更强的性质 ────────────────────────────
JS 侧必须在 setup project 上显式关掉 trace/video/screenshot，还得靠
globalTeardown 去删 error-context.md，否则输入框里的明文密码会被写进磁盘。

Python 侧不需要：pytest-playwright 的产物录制挂在**函数作用域**的 new_context
fixture 上，而 auth_state 用的是 browser.new_context() 直连 —— 那个 context
pytest-playwright 从头到尾看不见，因此不会为它写任何 trace / 录像 / 截图，
error-context.md 这种东西在 pytest 侧也不存在。泄露路径是被结构消掉的，
不是靠记得配开关。
"""

import json
import os
import shutil
import sys
import warnings
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auth_setup import credentials, export_state, login  # noqa: E402
from chrome_path import resolve_chrome      # noqa: E402

HERE = Path(__file__).resolve().parent
AUTH_DIR = HERE / ".auth"
STORAGE_STATE = AUTH_DIR / "state.json"
SESSION_STORAGE = AUTH_DIR / "session-storage.json"
TEST_RESULTS = HERE / "test-results"

# 对应 config 里的 expect: { timeout: 15_000 }
expect.set_options(timeout=15_000)


# ────────────────────────── 启动参数 ──────────────────────────

@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    args = dict(browser_type_launch_args)
    exe = resolve_chrome()
    if exe:
        args["executable_path"] = exe
    # 自签证书 / IP 直连 / 内网域名都需要这个
    args["args"] = [*args.get("args", []), "--ignore-certificate-errors"]
    return args


@pytest.fixture(scope="session")
def base_url(base_url):
    """命令行 --base-url 优先，其次 REC_BASE_URL。"""
    return base_url or os.environ.get("REC_BASE_URL")


# ────────────────────────── 登录一次 ──────────────────────────

@pytest.fixture(scope="session")
def auth_state(browser: Browser, base_url) -> Path | None:
    """对应 JS 侧的 setup project。整个 session 只跑一次。

    三种情形，行为不同 —— JS 版每次 `npm test` 都重跑 setup，会把手动登录
    导出的登录态覆盖掉；这里按凭据在不在来区分，两条路可以共存：

      有凭据（env 或 config.json）     → 自动登录，刷新登录态
      没凭据但 state.json 已存在        → 直接复用（手动登录 manual_login.py 的产物）
      没凭据也没 state.json            → 明确报错，把三条路都告诉你
    """
    # 凭据来源不止环境变量 —— config.json 的 auth 段同样算，见 rec_config.resolve_auth。
    # 注意不要把 credentials() 的结果绑到局部变量：后面 login() 一旦抛异常，
    # 这一帧的局部变量会进 --showlocals 的输出，密码就跟着出去了。
    has_creds = all(credentials().values())

    if not has_creds:
        if STORAGE_STATE.exists():
            print(f"复用已有登录态 {STORAGE_STATE}（没有可用凭据）")
            return STORAGE_STATE
        pytest.fail(
            "没有可用的登录态，也没有凭据。三选一：\n"
            "  1) export REC_USER=... REC_PASSWORD=...   然后重跑\n"
            "  2) 在 config.json 的 auth 段里填好（建议放 ~/.config/，chmod 600）\n"
            "  3) python manual_login.py                 （站点有验证码时用这条）",
            pytrace=False,
        )

    if not base_url:
        pytest.fail("缺少 base_url。设置 REC_BASE_URL 或传 --base-url。", pytrace=False)

    # 直连 browser.new_context()，绕开 pytest-playwright 的产物录制 ——
    # 见本文件开头关于凭据泄露的说明。这一行是那个性质的来源，别改成 new_context。
    context = browser.new_context(ignore_https_errors=True, base_url=base_url)
    page = context.new_page()
    page.set_default_navigation_timeout(90_000)
    try:
        login(page)
        export_state(context, page, AUTH_DIR)
    finally:
        context.close()
    return STORAGE_STATE


# ────────────────────────── context / page ──────────────────────────

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, base_url, auth_state, playwright):
    args = dict(browser_context_args)
    # 对应 JS 侧的 ...devices['Desktop Chrome']。不只是 viewport ——
    # 它还给出正常 Chrome 的 userAgent。无头模式默认的 UA 带 "HeadlessChrome"，
    # 按 UA 做网关的站点会因此走到另一条分支。
    device = playwright.devices.get("Desktop Chrome")
    if device:
        args.update({k: v for k, v in device.items() if k != "default_browser_type"})
    args["ignore_https_errors"] = True
    args["viewport"] = {"width": 1440, "height": 900}
    if base_url:
        args["base_url"] = base_url
    if auth_state and Path(auth_state).exists():
        args["storage_state"] = str(auth_state)
    return args


@pytest.fixture
def context(context):
    """对应 config 里的 actionTimeout / navigationTimeout。

    **不要改成 autouse 的 page fixture。** 那样写会让每一个用例都拉起
    page → browser_context_args → auth_state，于是项目里任何一个不碰浏览器的
    纯单元测试都被迫走一次完整登录，没配凭据时还会以「没有可用的登录态」这种
    完全无关的理由失败。

    挂在 context 上就只在用例真的用到浏览器时才生效；page 由 context 创建，
    自动继承这两个默认超时。
    """
    context.set_default_timeout(20_000)
    context.set_default_navigation_timeout(60_000)
    return context


@pytest.fixture
def authed_page(page: Page) -> Page:
    """带完整登录态的 page。

    storage_state 负责 cookies + localStorage；sessionStorage 得靠
    add_init_script 手动注回去，而且必须在**页面脚本执行之前** ——
    换成 page.evaluate() 就晚了，那时应用已经判定未登录并开始跳转。
    """
    if SESSION_STORAGE.exists():
        raw = SESSION_STORAGE.read_text(encoding="utf-8")
        # JS 的 addInitScript(fn, arg) 能把数据当参数传进去；Python 的
        # add_init_script **只收 script / path**，没有 arg 参数。
        # 所以数据要内联进脚本字符串 —— json.dumps 负责转义，
        # 别用 f-string 直接拼裸文本，登录态里的引号会把脚本弄坏。
        page.add_init_script(script="""(() => {
              const data = %s;
              try {
                for (const [k, v] of Object.entries(JSON.parse(data))) {
                  try { sessionStorage.setItem(k, v); } catch { /* 只读键或超配额 */ }
                }
              } catch { /* 文件损坏时不要拖垮整个用例 */ }
            })()""" % json.dumps(raw))
    return page


# ────────────────────────── 收尾 ──────────────────────────

def _scrub_auth_artifacts() -> None:
    """删掉登录相关的失败现场。

    JS 版这一步是必需的：Playwright 测试运行器会为 setup project 单独写一份
    error-context.md，里面是失败时的页面快照，**包含输入框里的明文密码**。

    Python 侧那条路径不存在（见文件开头）。这里保留清理动作是兜底：
    万一有人把登录逻辑改成走 page/context fixture，产物就又会被录下来。
    """
    if not TEST_RESULTS.exists():
        return
    removed = 0
    for entry in TEST_RESULTS.iterdir():
        if "auth" not in entry.name.lower() and "setup" not in entry.name.lower():
            continue
        shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink(missing_ok=True)
        removed += 1
    if removed:
        print(f"[teardown] 已清除 {removed} 份登录相关现场（可能含明文凭据）")


@pytest.fixture(scope="session", autouse=True)
def _auth_artifact_guard(pytestconfig):
    # --showlocals / -l 会把栈帧里的局部变量原样打印出来。auth_setup.py 刻意
    # 不把密码绑定到局部变量，但用例自己写的代码不受这个约束。
    if pytestconfig.getoption("showlocals", default=False) and credentials()["password"]:
        warnings.warn(
            "同时启用了 --showlocals 且存在可用凭据："
            "失败时的局部变量会被打印到终端和 CI 日志里，可能包含明文凭据。",
            stacklevel=1,
        )
    yield
    _scrub_auth_artifacts()
