"""P2.10 回放节奏 / 导航等待：慢导航（>5s 且 <20s）下双链必须同判。

场景（quality-log 矩阵唯一整行空白：「回放节奏 / 导航等待」，P2.8 风险 #2）：

录制时同页导航花费 5–20s（慢服务器 / setTimeout 迟跳），生成链的
wait_for_recorded_url / recorded_page_for 用 20s 导航预算（rec_helpers
缺省），而 trace 链的 _page_for_node 把**单步定位预算**（timeout_ms，
缺省 5s）同时用在「等导航完成」上 —— 同一份录制：

  * 生成链（pytest）：20s 预算 → 等到导航完成 → PASS；
  * trace 链：5s 预算 → 导航还没 commit → 路线不符 → FAIL。

双链对同一录制给出相反结论，违反验收门禁「pytest 与 trace 两条回放链
必须对相同断言给出相同结果」。两类导航形态都受影响：

  T1 同页慢导航：点击按钮 setTimeout 跳 /slow-next，服务器延迟 7s；
  T2 慢 popup：target=_blank 打开 /slow-popup，服务器延迟 7s。

TDD 红线：先红后绿 —— 修复前两条测试都必须以「trace 链 failed /
pytest 链 passed」的分歧形态失败；修复（导航等待改用与生成链一致的
导航预算）后双链同绿；再以篡改 expected 证明测试不空转。
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

SLOW_DELAY = 7.0          # > trace 链修复前的 5s 单步预算，< 生成链 20s 导航预算
TRACE_TIMEOUT_MS = 5_000  # 显式传缺省值，让预算差成为确定性事件而非偶发

PAGE_MAIN = """<!doctype html><html><head><meta charset="utf-8">
<title>pacing-main</title></head><body>
<input id="q" placeholder="keyword">
<button id="go" onclick="setTimeout(function(){{location.href='/slow-next';}},300)">slow go</button>
<a id="open" href="/slow-popup" target="_blank">slow popup</a>
<div id="marker">main</div>
</body></html>"""

PAGE_SLOW_NEXT = """<!doctype html><html><head><meta charset="utf-8">
<title>slow-next</title></head><body>
<input id="q" placeholder="keyword">
<div id="marker">slow-next</div>
</body></html>"""

PAGE_SLOW_POPUP = """<!doctype html><html><head><meta charset="utf-8">
<title>slow-popup</title></head><body>
<div id="marker">popup-slow</div>
</body></html>"""


@pytest.fixture(scope="module")
def slow_origin():
    """单端口服务器：/slow-next 与 /slow-popup 故意延迟 7s 才响应。

    导航提交时间 ≈ setTimeout 300ms + 7s ≫ trace 链修复前的 5s 预算，
    使「pytest 链已等到、trace 链已放弃」成为确定事件而非竞态。
    """

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith(("/slow-next", "/slow-popup")):
                time.sleep(SLOW_DELAY)
            body = (PAGE_SLOW_POPUP if self.path.startswith("/slow-popup")
                    else PAGE_SLOW_NEXT if self.path.startswith("/slow-next")
                    else PAGE_MAIN).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()


_UI = {"ui": {"templates": {"element": "act.png"},
              "pageRect": {"x": 0, "y": 0, "width": 60, "height": 20},
              "pageViewport": {"width": 1280, "height": 720},
              "deviceScaleFactor": 1}}


def _step(**kw):
    base = {
        "id": "s", "t": 1000, "type": "click", "sel": 'locator("#x")',
        "kind": "id", "url": "/", "pageId": "p1",
        "pageOrigin": "http://127.0.0.1:8000",
    }
    base.update(kw)
    return base


def _run_spec(spec_text, page, tag):
    ns = {"__file__": __file__}
    exec(compile(spec_text, tag, "exec"), ns)
    test_fn = next(v for k, v in ns.items()
                   if k.startswith("test_") and callable(v))
    test_fn(page)


def _browser(pw):
    from chrome_path import resolve_chrome
    return pw.chromium.launch(headless=True, executable_path=resolve_chrome())


def test_slow_same_page_nav_dual_chain_agree(slow_origin):
    """T1 同页慢导航：fill → 点按钮（300ms 后跳 /slow-next，7s 才响应）
    → 在 /slow-next 上 fill + 断言。生成链与 trace 链必须同绿。"""
    from generate_spec import generate_spec
    from generate_trace import generate_trace
    from replay_trace import replay_trace
    from playwright.sync_api import sync_playwright

    origin = slow_origin
    steps = [
        _step(id="f1", t=1000, type="fill", sel='locator("#q")', kind="id",
              value="hello", url="/", pageId="p1", pageOrigin=origin),
        _step(id="nav", t=1500, type="click", sel='locator("#go")',
              kind="id", url="/", pageId="p1", pageOrigin=origin, **_UI),
        _step(id="f2", t=9000, type="fill", sel='locator("#q")', kind="id",
              value="world", url="/slow-next", pageId="p1", pageOrigin=origin),
        _step(id="as1", t=9500, type="assert", sel='locator("#q")', kind="id",
              assertion="value", expected="world", op="equals",
              expectedConfirmed=True, url="/slow-next", pageId="p1",
              pageOrigin=origin),
    ]
    spec = generate_spec(steps, [], start_url=origin + "/", name="pacing-nav")
    trace = generate_trace(steps, [], name="pacing-nav",
                           start_url=origin + "/")
    # 生成链必须为 /slow-next 编译出完整 URL 等待（含 origin）——
    # 这是它 20s 预算的载体；没有等待行的话本测试测的不是目标缝隙。
    assert 'wait_for_recorded_url(page, ' in spec, spec

    with sync_playwright() as pw:
        browser = _browser(pw)
        ctx = browser.new_context(base_url=origin + "/")

        # ── 生成链：必须等到导航完成再 fill/断言 ──
        page = ctx.new_page()
        page.goto(origin + "/")
        _run_spec(spec, page, "pacing-nav.py")
        assert page.url.endswith("/slow-next"), page.url
        assert page.locator("#q").input_value() == "world", (
            "生成链：/slow-next 上的 fill 未生效 —— 导航未被等待，"
            "fill 落在导航前的页面上被导航抹掉")
        print("pytest chain: PASS (slow same-page nav waited)")

        # ── trace 链：同一录制必须同样成功（双链同判）──
        page2 = ctx.new_page()
        page2.goto(origin + "/")
        execution = replay_trace(page2, trace, navigate=False,
                                 timeout_ms=TRACE_TIMEOUT_MS)
        assert execution["status"] == "success", (
            f"trace 链对同一录制给出相反结论（双链分歧）：\n"
            + json.dumps(execution["steps"], ensure_ascii=False, indent=2))
        assert page2.url.endswith("/slow-next"), page2.url
        assert page2.locator("#q").input_value() == "world"
        print("trace chain: PASS (slow same-page nav waited)")

        # ── 反向：篡改 expected（world → tampered）→ 双链都必须失败 ──
        steps_bad = json.loads(json.dumps(steps))
        for s in steps_bad:
            if s.get("id") == "as1":
                s["expected"] = "tampered"
        spec_bad = generate_spec(steps_bad, [], start_url=origin + "/",
                                 name="pacing-nav-bad")
        trace_bad = generate_trace(steps_bad, [], name="pacing-nav-bad",
                                   start_url=origin + "/")
        page3 = ctx.new_page()
        page3.goto(origin + "/")
        try:
            _run_spec(spec_bad, page3, "pacing-nav-bad.py")
            raise SystemExit("反向验证失败：篡改 expected 后生成链居然通过")
        except AssertionError:
            print("pytest chain tampered: FAIL as expected")
        page4 = ctx.new_page()
        page4.goto(origin + "/")
        exec_bad = replay_trace(page4, trace_bad, navigate=False,
                                timeout_ms=TRACE_TIMEOUT_MS)
        assert exec_bad["status"] != "success", "篡改后 trace 链居然通过"
        print("trace chain tampered: FAIL as expected")

        browser.close()


def test_slow_popup_dual_chain_agree(slow_origin):
    """T2 慢 popup：target=_blank 打开 /slow-popup（7s 才响应）→ 在 popup
    上断言文本。生成链与 trace 链必须同绿。"""
    from generate_spec import generate_spec
    from generate_trace import generate_trace
    from replay_trace import replay_trace
    from playwright.sync_api import sync_playwright

    origin = slow_origin
    steps = [
        _step(id="open", t=1000, type="click", sel='locator("#open")',
              kind="id", url="/", pageId="p1", pageOrigin=origin, **_UI),
        _step(id="as1", t=9000, type="assert", sel='locator("#marker")',
              kind="id", assertion="text", expected="popup-slow",
              op="equals", expectedConfirmed=True, url="/slow-popup",
              pageId="p2", pageOrigin=origin, openerPageId="p1"),
    ]
    spec = generate_spec(steps, [], start_url=origin + "/", name="pacing-popup")
    trace = generate_trace(steps, [], name="pacing-popup",
                           start_url=origin + "/")

    with sync_playwright() as pw:
        browser = _browser(pw)
        ctx = browser.new_context(base_url=origin + "/")

        # ── 生成链：recorded_page_for 以 20s 预算等 popup 到达录制 URL ──
        # 生成的用例把 popup 存进 recorded_pages["p2"]，popup 上的断言锚在
        # 那个页对象上 —— opener 的 page 变量停在首页，不能用它验证终态。
        page = ctx.new_page()
        page.goto(origin + "/")
        _run_spec(spec, page, "pacing-popup.py")
        popups = [p for p in ctx.pages if p.url.endswith("/slow-popup")]
        assert popups, [p.url for p in ctx.pages]
        assert popups[0].locator("#marker").inner_text() == "popup-slow"
        print("pytest chain: PASS (slow popup waited)")

        # ── trace 链：popup 解析等待必须与生成链同预算 ──
        page2 = ctx.new_page()
        page2.goto(origin + "/")
        execution = replay_trace(page2, trace, navigate=False,
                                 timeout_ms=TRACE_TIMEOUT_MS)
        assert execution["status"] == "success", (
            f"trace 链对同一录制给出相反结论（双链分歧）：\n"
            + json.dumps(execution["steps"], ensure_ascii=False, indent=2))
        popups2 = [p for p in ctx.pages if p.url.endswith("/slow-popup")]
        assert popups2, [p.url for p in ctx.pages]
        assert popups2[0].locator("#marker").inner_text() == "popup-slow"
        print("trace chain: PASS (slow popup waited)")

        browser.close()
