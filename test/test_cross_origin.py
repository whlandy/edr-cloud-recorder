"""同主机不同端口 / 跨 origin 导航的录制与回放缺陷（失败测试先行）。

场景（quality-log 矩阵「同主机不同端口 / 跨 origin」缺口）：

真实场景是常见形态 —— 同一标签页的操作把浏览器带到同主机不同端口的
页面上（登录跳转、内嵌旧服务等）。录制器的步骤记 url=pathname+search+hash，
跨 origin 同路径导航在 url 上完全不可见，唯一能区分它们的是 pageOrigin。
逐项检查后发现两处真实缺陷：

D1 生成侧（generate_spec.py）：「同页地址变化 → wait_for_recorded_url」
   的判定只比较 pathname+search+hash，**不比较 pageOrigin**。跨 origin
   同路径导航（端口 A / → 端口 B /）不产生任何等待 —— 生成的 pytest 在
   导航完成前就对属于 B 的步骤先行操作；断言甚至可能落在 A 页面上
   假绿，随后导航把状态抹掉。trace 链（_page_for_node 的
   _page_matches_node 校验 origin）会等待到导航完成：两条链对同一份
   录制给出相反结论 —— 正是双链一致性门禁要抓的形态。

D2 录制侧（record.py）：pageOrigin 由泵循环事后补写（resolve_page_identity
   读 page.url，而 on_ready 驱动路径下只在显式 pump() 时结算）。步骤在
   A 上产生、导航到 B 之后才被 pump 结算时，**A 上的步骤被记到 B 的
   origin 名下**。afterStepId 曾有同款缺陷（P2.2 轮），离线手拼步骤
   测不出，只有真实 record_session 泵节奏才暴露。

TDD 红线（任务门禁）：本文件全部先失败，再最小修复转绿。

* T1 生成侧：跨 origin 同路径导航步骤必须编译出带 origin 的
  wait_for_recorded_url；同 origin 同路径不得引入多余等待（防放宽）。
* T2 录制侧：真实 record_session 泵节奏下，A 上产生的 fill 步骤的
  pageOrigin 必须仍是 A（导航发生在步骤之后）。
* T3 回放侧：双链同判 —— A 填值 → 跳 B（同路径，B 故意慢 1.2s 让竞态
  确定）→ B 填值断言。修复前生成链的 fill/断言落在 A 上（假绿），
  导航完成后输入框为空；修复后两链都必须等导航真正完成再操作。
"""

import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

PAGE_T = """<!doctype html><html><head><meta charset="utf-8">
<title>origin-fixture</title></head><body>
<input id="q" placeholder="keyword">
<a id="to-port-b" href="{b}/">go to port B</a>
<button id="go" onclick="setTimeout(function(){{location.href='{b}/';}},800)">delayed go</button>
<div id="marker">origin-a</div>
</body></html>"""


@pytest.fixture(scope="module")
def two_origins():
    """两个端口跑同一份页面模板：A 首页的链接指向 B 首页（同路径 /）。

    B 的响应故意延迟 1.2s：让「点击后、导航完成前」的窗口足够长，
    使生成链不等待时的竞态（fill 落在 A 上）成为确定事件而非偶发。
    """
    state = {"b": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            time.sleep(getattr(self.server, "rec_delay", 0))
            body = PAGE_T.format(b=state["b"] or "PLACEHOLDER").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    servers = [HTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
    for srv in servers:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    a_srv, b_srv = servers
    a_srv.rec_delay, b_srv.rec_delay = 0.0, 1.2
    a = f"http://127.0.0.1:{a_srv.server_port}"
    b = f"http://127.0.0.1:{b_srv.server_port}"
    state["b"] = b
    try:
        yield a, b
    finally:
        for srv in servers:
            srv.shutdown()


# ── T1 生成侧：跨 origin 同路径导航必须产生完整 URL 等待 ──────────────────

def _step(**kw):
    base = {
        "id": "s", "t": 1000, "type": "click", "sel": 'locator("#x")',
        "kind": "id", "url": "/", "pageId": "p1",
        "pageOrigin": "http://127.0.0.1:8000",
    }
    base.update(kw)
    return base


def test_cross_origin_same_path_yields_wait_for_recorded_url():
    """同一 pageId 的步骤从端口 A 的 / 走到端口 B 的 /：
    必须生成 wait_for_recorded_url(含 B 的完整 URL)，证明生成器看见 origin。

    现状（缺陷 D1）：地址变化判定只比 pathname，/ == / → 不产生等待，
    生成的用例在导航完成前就继续执行。
    """
    from generate_spec import generate_spec

    a, b = "http://127.0.0.1:8000", "http://127.0.0.1:9000"
    steps = [
        _step(id="c1", t=1000, pageOrigin=a),
        _step(id="c2", t=2000, pageOrigin=b, sel='locator("#y")'),
    ]
    spec = generate_spec(steps, [], start_url=a + "/", name="xorigin")
    wait_calls = re.findall(r"wait_for_recorded_url\(page, ([^)]+)\)", spec)
    assert any(b in call for call in wait_calls), (
        f"跨 origin 同路径导航未生成带 origin 的等待；spec:\n{spec}"
    )


def test_same_origin_same_path_does_not_double_wait():
    """同 origin 同路径不因本修复引入重复等待（防回归：判定不能放宽成
    每步都等）。"""
    from generate_spec import generate_spec

    a = "http://127.0.0.1:8000"
    steps = [_step(id="c1", t=1000, pageOrigin=a),
             _step(id="c2", t=2000, pageOrigin=a, sel='locator("#y")')]
    spec = generate_spec(steps, [], start_url=a + "/", name="same")
    wait_calls = re.findall(r"wait_for_recorded_url\(page, ([^)]+)\)", spec)
    assert not wait_calls, (
        f"同 origin 同路径不应产生地址变化等待；spec:\n{spec}"
    )


# ── T2 录制侧：泵节奏下 pageOrigin 必须是步骤发生时的 origin ──────────────

def test_recorded_step_origin_is_step_time_origin(two_origins, tmp_path):
    """A 首页填值（blur 落账）→ 点链接跳 B → B 加载完成后才 pump。

    缺陷形态（D2）：fill 步骤在 A 上产生，但身份结算发生在导航之后
    （pump 读 page.url），pageOrigin 被补写成 B。生成器据此把 A 上的
    步骤当 B 的步骤转译。on_ready 驱动路径下身份只在显式 pump() 时
    结算 —— 离线手拼步骤永远测不出，必须真跑 record_session。
    """
    from playwright.sync_api import sync_playwright

    from chrome_path import resolve_chrome
    from record import record_session

    a, b = two_origins

    def drive(page, pump):
        pump()                          # 起始帧，无待结算步骤
        page.click("#q")
        page.fill("#q", "hello")        # change 在 blur 时落账
        page.click("#marker")           # blur → fill 步骤入队（身份待结算）
        # 注意：这里**不 pump** —— 真实用户操作节奏里，导航可能发生在
        # 泵结算之前。这正是缺陷的触发窗口。
        page.click("#to-port-b")        # 导航到 B
        page.wait_for_load_state("load")
        page.wait_for_timeout(300)
        pump()                          # 此刻 page.url 已是 B → 污染 fill

    result = record_session(
        start_url=a + "/", name="xorigin-record",
        out_dir=str(tmp_path), headless=True, on_ready=drive,
    )
    steps = result["steps"]
    fill_steps = [s for s in steps if s.get("type") == "fill"]
    assert fill_steps, f"fill 步骤未被录制：{steps}"
    assert fill_steps[0].get("pageOrigin") == a, (
        f"A 上的 fill 步骤 pageOrigin 被记成导航后的 origin："
        f"{fill_steps[0].get('pageOrigin')!r}（应为 {a!r}）；步骤：{fill_steps[0]}"
    )


# ── T3 回放侧：双链同判 —— 跨 origin 导航必须真实发生 ─────────────────────

def test_dual_chain_cross_origin_nav_wait(two_origins):
    """A 填值 → 点链接跳 B（同路径，B 慢 1.2s）→ B 填值 → B 断言输入值。

    生成链必须等 URL（含 origin）变成 B 首页后才在 B 上 fill；否则
    fill/断言落在导航前的 A 页面上（断言假绿），导航完成后状态被抹掉。
    trace 链由 _page_for_node 的 origin 校验等待 —— 修复前两链结论相反。
    """
    from playwright.sync_api import sync_playwright

    from chrome_path import resolve_chrome
    from generate_spec import generate_spec
    from generate_trace import generate_trace
    from replay_trace import replay_trace

    a, b = two_origins
    # 导航形态：按钮 setTimeout 800ms 后跳 B。点击本身不导航 —— 生成链
    # 若不等待跨 origin 地址变化，后续 fill/断言全部落在 A 页面上假绿，
    # 800ms 后被跳转抹掉。这是 D1 缺陷的确定性触发形态。
    _ui = {"ui": {"templates": {"element": "nav.png"},
                  "pageRect": {"x": 0, "y": 0, "width": 60, "height": 20},
                  "pageViewport": {"width": 1280, "height": 720},
                  "deviceScaleFactor": 1}}
    steps = [
        _step(id="f1", t=1000, type="fill", sel='locator("#q")', kind="id",
              value="hello", url="/", pageId="p1", pageOrigin=a),
        _step(id="nav", t=1500, type="click", sel='locator("#go")',
              kind="id", url="/", pageId="p1", pageOrigin=a, **_ui),
        _step(id="f2", t=2000, type="fill", sel='locator("#q")', kind="id",
              value="world", url="/", pageId="p1", pageOrigin=b),
        _step(id="as1", t=2500, type="assert", sel='locator("#q")', kind="id",
              assertion="value", expected="world", op="equals",
              expectedConfirmed=True, url="/", pageId="p1", pageOrigin=b),
    ]
    spec = generate_spec(steps, [], start_url=a + "/", name="dual")
    trace = generate_trace(steps, [], name="dual", start_url=a + "/")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
                                     executable_path=resolve_chrome())
        # 生成的 spec 起始导航是相对路径（page.goto("/")），约定由 base_url
        # 提供录制起点 origin —— 与 conftest 的回放会话约定一致。
        ctx = browser.new_context(base_url=a + "/")
        page = ctx.new_page()
        page.goto(a + "/")
        ns = {"__file__": str(__file__)}
        exec(compile(spec, "dual.py", "exec"), ns)
        test_fn = next(v for k, v in ns.items()
                       if k.startswith("test_") and callable(v))
        test_fn(page)
        # 等导航真正完成（到 B）再检查终态 —— 修复前 fill/断言落在 A 上
        # 假绿，导航完成后 B 的输入框是空的。
        page.wait_for_url(lambda u: str(u).startswith(b), timeout=10_000)
        assert page.locator("#q").input_value() == "world", (
            "生成链：B 上的 fill 未生效 —— 跨 origin 导航未被等待，"
            "fill 落在导航前的页面上被导航抹掉"
        )
        # 回放链：同一条轨迹必须同样成功（双链同判）
        page2 = ctx.new_page()
        page2.goto(a + "/")
        execution = replay_trace(page2, trace, navigate=False, timeout_ms=8000)
        assert execution["status"] == "success", execution["steps"]
        page2.wait_for_url(lambda u: str(u).startswith(b), timeout=10_000)
        assert page2.locator("#q").input_value() == "world"
        browser.close()
