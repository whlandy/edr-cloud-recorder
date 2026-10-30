"""P2.11 网络断言/写请求校验 × 慢响应（5–30s）：双链必须同判。

场景（quality-log P2.10 风险 #3 的收窄与实证：慢导航 × 网络断言
共存下观察窗/等待预算的语义。调研结论：count=0 观察窗两链都在因果
动作前后创建/轮询，窗语义一致；真正的缝隙在**响应等待预算**）：

录制/校验链路里有一个隐含的预算错位，形态与 P2.10 的导航等待完全
同源（「导航/网络等待」错用「单步元素定位」的预算）：

  * 生成链（pytest）：`page.context.expect_event("request", ...)` 用
    Playwright 缺省 30s；抓到 request 后 `req.response()`（channel 调用，
    无 timeout 参数）也没有 5s 上限 —— 7s 才返回的写响应照常等到。
  * trace 链（replay_trace）：`_response_waiter(page, pred, timeout_ms)`
    把**单步定位预算** timeout_ms（缺省 5s）同时用在响应等待上 ——
    同一个 7s 响应，5s 就放弃。

同一份录制：pytest 链 PASS、trace 链 TimeoutError（playwright 的
「Timeout 5000ms exceeded while waiting for event "response"」，属
automation 类）→ 双链对同一断言给出相反结论，违反验收门禁「两条回放
链必须对相同断言给出相同结果」。

TDD 红线：先红后绿 —— 修复前 T1 以「trace 链 TimeoutError / 生成链
PASS」的分歧形态失败；修复（trace 链响应等待改用与生成链一致的网络
响应预算）后双链同绿；再以篡改 expected（状态码）证明测试不空转。

反例 fixture：点击 #save 发 POST /api/save，服务器故意延迟 7s 才回
201 —— 大于 trace 链的 5s 单步预算、小于生成链的 30s 缺省。
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

SLOW_RESP_DELAY = 7.0      # > trace 链 5s 单步预算，< 生成链 30s 缺省
TRACE_TIMEOUT_MS = 5_000   # 显式传缺省值，让预算差成为确定事件而非偶发

PAGE_MAIN = """<!doctype html><html><head><meta charset="utf-8">
<title>slow-resp-main</title></head><body>
<input id="q" placeholder="keyword">
<button id="save" onclick="fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({p:1})})">save</button>
<div id="marker">main</div>
</body></html>"""


@pytest.fixture(scope="module")
def slow_resp_origin():
    """单端口服务器：POST /api/save 故意延迟 7s 才回 201。"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            if self.path.startswith("/api/save"):
                time.sleep(SLOW_RESP_DELAY)
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            body = b"{}"
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            body = PAGE_MAIN.encode("utf-8")
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


def _save_step(origin, step_id="save", t=1000):
    return {
        "id": step_id, "t": t, "type": "click", "sel": 'locator("#save")',
        "kind": "id", "url": "/", "pageId": "p1", "pageOrigin": origin,
        "ui": {"templates": {"element": "act.png"},
               "pageRect": {"x": 0, "y": 0, "width": 60, "height": 20},
               "pageViewport": {"width": 1280, "height": 720},
               "deviceScaleFactor": 1},
    }


def _net_events(origin, t_req=1100, t_res=1150):
    """#save 点击触发的写请求对：录到 201（响应 7s 后才回来）。"""
    return [
        {"id": 1, "t": t_req, "phase": "req", "method": "POST",
         "url": origin + "/api/save", "body": '{"p":1}'},
        {"requestId": 1, "t": t_res, "phase": "res", "method": "POST",
         "url": origin + "/api/save", "status": 201},
    ]


def _run_spec(spec_text, page, tag):
    ns = {"__file__": __file__}
    exec(compile(spec_text, tag, "exec"), ns)
    test_fn = next(v for k, v in ns.items()
                   if k.startswith("test_") and callable(v))
    test_fn(page)


def _browser(pw):
    from chrome_path import resolve_chrome
    return pw.chromium.launch(headless=True, executable_path=resolve_chrome())


def test_slow_write_response_dual_chain_agree(slow_resp_origin):
    """T1 慢写响应（无用户网络断言）：点 #save 发 POST，7s 后 201。

    生成链（缺省 30s request 等待 + 无上限 response()）必须等到；
    trace 链的响应等待不能被 5s 单步预算掐断 —— 双链同绿。"""
    from generate_spec import generate_spec
    from generate_trace import generate_trace
    from replay_trace import replay_trace
    from playwright.sync_api import sync_playwright

    origin = slow_resp_origin
    steps = [_save_step(origin)]
    net = _net_events(origin)
    spec = generate_spec(steps, net, start_url=origin + "/", name="slow-resp")
    trace = generate_trace(steps, net, name="slow-resp",
                           start_url=origin + "/")
    # trace 的 responses 期望必须真的带着这条 POST —— 没有它本测试测的
    # 不是目标缝隙（trace 链根本不会等响应）。
    node0 = trace["step_0001"]["attach"]["verification"]["responses"][0]
    assert node0["method"] == "POST" and "/api/save" in node0["url"], node0
    assert node0["expectedStatus"] == 201

    with sync_playwright() as pw:
        browser = _browser(pw)
        ctx = browser.new_context(base_url=origin + "/")

        # ── 生成链：7s 慢响应必须等到（Playwright 缺省 30s + 无上限 response()）──
        page = ctx.new_page()
        page.goto(origin + "/")
        _run_spec(spec, page, "slow-resp.py")
        print("pytest chain: PASS (slow write response waited)")

        # ── trace 链：同一录制必须同样成功（双链同判）──
        page2 = ctx.new_page()
        page2.goto(origin + "/")
        t0 = time.monotonic()
        execution = replay_trace(page2, trace, navigate=False,
                                 timeout_ms=TRACE_TIMEOUT_MS)
        elapsed = time.monotonic() - t0
        assert execution["status"] == "success", (
            f"trace 链对同一录制给出相反结论（双链分歧）：\n"
            + json.dumps(execution["steps"], ensure_ascii=False, indent=2))
        print(f"trace chain: PASS (slow write response waited, {elapsed:.1f}s)")

        # ── 反向：篡改自动写断言的期望（请求体 p:1 → p:2）→ 双链都必须失败 ──
        # （状态码不能当篡改位：generate_spec 对 ≥400 的响应不生成自动写断言，
        # 改状态只会让断言消失 —— 这是录制语义，不是回放分歧。）
        steps_bad = json.loads(json.dumps(steps))
        net_bad = json.loads(json.dumps(net))
        for n in net_bad:
            if n.get("phase") == "req":
                n["body"] = '{"p":2}'
        spec_bad = generate_spec(steps_bad, net_bad, start_url=origin + "/",
                                 name="slow-resp-bad")
        trace_bad = generate_trace(steps_bad, net_bad, name="slow-resp-bad",
                                   start_url=origin + "/")
        page3 = ctx.new_page()
        page3.goto(origin + "/")
        with pytest.raises(Exception) as ei:
            _run_spec(spec_bad, page3, "slow-resp-bad.py")
        print("pytest chain tampered: FAIL as expected →",
              f"{type(ei.value).__name__}: {str(ei.value)[:120]}")
        page4 = ctx.new_page()
        page4.goto(origin + "/")
        exec_bad = replay_trace(page4, trace_bad, navigate=False,
                                timeout_ms=TRACE_TIMEOUT_MS)
        assert exec_bad["status"] != "success", "篡改后 trace 链居然通过"
        print("trace chain tampered: FAIL as expected")

        browser.close()


def test_slow_response_with_network_assert_dual_chain_agree(slow_resp_origin):
    """T2 慢写响应 + 用户网络断言（status=201 绑同一条 POST）：断言落在
    等待块里，响应 7s 才回 —— 双链都必须等到响应后再断，且同判。"""
    from generate_spec import generate_spec
    from generate_trace import generate_trace
    from replay_trace import replay_trace
    from playwright.sync_api import sync_playwright

    origin = slow_resp_origin
    steps = [
        _save_step(origin),
        {
            "id": "st-1", "t": 2000, "type": "assert", "scope": "network",
            "assertion": "status", "expected": 201,
            "reqMethod": "POST", "reqUrl": "/api/save",
            "expectedConfirmed": True, "afterStepId": "save",
            "url": "/", "pageId": "p1", "pageOrigin": origin,
        },
    ]
    net = _net_events(origin)
    spec = generate_spec(steps, net, start_url=origin + "/",
                         name="slow-resp-assert")
    trace = generate_trace(steps, net, name="slow-resp-assert",
                           start_url=origin + "/")
    # 用户断言必须真的合并进响应校验（expectedStatus 201）——否则测的是
    # 自动断言而非用户契约。
    node0 = trace["step_0001"]["attach"]["verification"]["responses"][0]
    assert node0["expectedStatus"] == 201, node0
    assert node0.get("userAssertId") == "st-1", node0

    with sync_playwright() as pw:
        browser = _browser(pw)
        ctx = browser.new_context(base_url=origin + "/")

        page = ctx.new_page()
        page.goto(origin + "/")
        _run_spec(spec, page, "slow-resp-assert.py")
        print("pytest chain: PASS (slow response + network assert)")

        page2 = ctx.new_page()
        page2.goto(origin + "/")
        execution = replay_trace(page2, trace, navigate=False,
                                 timeout_ms=TRACE_TIMEOUT_MS)
        assert execution["status"] == "success", (
            f"trace 链对同一录制给出相反结论（双链分歧）：\n"
            + json.dumps(execution["steps"], ensure_ascii=False, indent=2))
        print("trace chain: PASS (slow response + network assert)")

        # ── 反向：篡改用户断言 expected（201 → 500，net 事件不动）→
        # 双链都必须失败（实际响应 201，断言 500 必须红）──
        steps_bad = json.loads(json.dumps(steps))
        for s in steps_bad:
            if s.get("id") == "st-1":
                s["expected"] = 500
        spec_bad = generate_spec(steps_bad, net, start_url=origin + "/",
                                 name="slow-resp-assert-bad")
        trace_bad = generate_trace(steps_bad, net, name="slow-resp-assert-bad",
                                   start_url=origin + "/")
        page3 = ctx.new_page()
        page3.goto(origin + "/")
        with pytest.raises(Exception) as ei:
            _run_spec(spec_bad, page3, "slow-resp-assert-bad.py")
        print("pytest chain tampered: FAIL as expected →",
              f"{type(ei.value).__name__}: {str(ei.value)[:120]}")
        page4 = ctx.new_page()
        page4.goto(origin + "/")
        exec_bad = replay_trace(page4, trace_bad, navigate=False,
                                timeout_ms=TRACE_TIMEOUT_MS)
        assert exec_bad["status"] != "success", "篡改后 trace 链居然通过"
        print("trace chain tampered: FAIL as expected")

        browser.close()
