"""P2.7 popup × 网络断言（scope=network）失败测试（quality-log 矩阵缺口）。

连续五轮遗留的最高价值缺口：scope=network 断言与 popup/新标签页的组合。
录制侧（record.py:569 on_request）在 **browser context 级**捕获请求 —— popup
里发出的请求照常进菜单、照常可绑定断言；断言步骤由 injector 在 popup 页面
上落盘（pageId=popup、pageOrigin=popup origin）。但两条回放链：

* 生成链 `_emit_network_assert` 的 `NetRequestWatcher(page, ...)` 和自动写
  请求等待 `page.expect_request(...)` 都挂在**因果动作所在的 page**上 ——
  请求若由 popup 发出（动作是「打开 popup」、断言绑 popup 的请求），opener
  的 watcher 永远数不到它，`request_count=0` 负断言假绿；
* trace 链 `replay_trace` 的 request_count 分支同样只监听当前 node_page。

先写测试：每条先失败，再做最小实现。min fix 目标：观察口径与录制侧一致
—— 录制时 context 级什么请求都能看到，回放的负断言就必须 context 级看
（「不应发请求」的「不」不能被回放端的观察窗口缩小）。
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from chrome_path import resolve_chrome

# ── 本地 HTTP fixture：opener 页上按钮打开 popup；popup 页按钮延迟发 POST ──
# 因果链：点击 opener 的 #open（打开 popup）→ popup 的 #go（延迟 250ms 发
# POST /api/save）。用户在 popup 页上录「不应发请求」断言（scope=network，
# pageId=popup）。观察窗的因果动作是「打开 popup」—— 弹出的页面在窗内
# 发请求，负断言必须数到（现在 opener 级 watcher 数不到 → 假绿）。


def _start_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/popup_load"):
                # 正向网络断言的缺陷形态：popup 打开后**加载时**发 POST /api/save。
                # Chrome 88+ 对 target=_blank 隐式 rel=noopener，popup 内外脚本分流；
                # 这里 popup 自身 fetch，opener 页的 page.expect_request 永远看不到它。
                body = (b"<!doctype html><html><head><meta charset='utf-8'>"
                        b"<title>popup_load</title></head><body>"
                        b"<div id='marker'>pl</div>"
                        b"<script>setTimeout(function(){"
                        b"fetch('/api/save',{method:'POST',"
                        b"body:JSON.stringify({p:'1'})})},150)</script>"
                        b"</body></html>")
            elif self.path.startswith("/popup"):
                body = (b"<!doctype html><html><head><meta charset='utf-8'>"
                        b"<title>popup</title></head><body>"
                        b"<button id='go' onclick=\"setTimeout(function(){"
                        b"fetch('/api/save',{method:'POST',body:'p=1'})},250)\">"
                        b"popup go</button><div id='marker'>p</div></body></html>")
            else:
                body = (b"<!doctype html><html><head><meta charset='utf-8'>"
                        b"<title>opener</title></head><body>"
                        b"<a id='open' href='/popup' target='_blank'>open popup</a>"
                        b"<a id='openload' href='/popup_load' target='_blank'>"
                        b"open load-popup</a>"
                        b"<div id='marker'>o</div></body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(201)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


@pytest.fixture(scope="module")
def popup_origin():
    srv, origin = _start_server()
    try:
        yield origin
    finally:
        srv.shutdown()


# ── 步骤构造：动作在 opener，断言绑在 popup 页上 ───────────────────────────


def _open_popup_step(origin):
    return {
        "id": "act-1", "t": 1000, "type": "click",
        "sel": 'locator("#open")', "kind": "css", "url": "/", "pageId": "p1",
        "pageOrigin": origin,
        "ui": {"templates": {"element": "act-1.png"},
               "pageRect": {"x": 0, "y": 0, "width": 50, "height": 20},
               "pageViewport": {"width": 1280, "height": 720},
               "deviceScaleFactor": 1},
    }


def _popup_click_step(origin):
    """popup 里的点击（pageId=p2，openerPageId=p1）。"""
    return {
        "id": "act-2", "t": 1600, "type": "click",
        "sel": 'locator("#go")', "kind": "css", "url": "/popup",
        "pageId": "p2", "openerPageId": "p1", "pageOrigin": origin,
        "ui": {"templates": {"element": "act-2.png"},
               "pageRect": {"x": 0, "y": 0, "width": 50, "height": 20},
               "pageViewport": {"width": 1280, "height": 720},
               "deviceScaleFactor": 1},
    }


def _count_zero_step(step_id="rc-p1", confirmed=True, after="act-1",
                     origin="http://x", url_part="/api/save", page_id="p2"):
    """popup 页上落盘的 count=0 断言：pageId=popup，因果动作是打开 popup。"""
    return {
        "id": step_id, "t": 2600, "type": "assert", "scope": "network",
        "assertion": "request_count", "expected": 0,
        "reqMethod": "POST", "reqUrl": url_part,
        "expectedConfirmed": confirmed,
        "afterStepId": after,
        "url": "/popup", "pageId": page_id, "pageOrigin": origin,
        "wait": {"timeoutMs": 3000, "pollMs": 50, "stableMs": 300},
    }


def _status_step(step_id="st-p1", expected=201, after="act-1",
                 origin="http://x", url_part="/api/save", page_id="p2"):
    """popup 页上落盘的 status=201 断言（正向：必须能绑到 popup 发的请求）。"""
    return {
        "id": step_id, "t": 2700, "type": "assert", "scope": "network",
        "assertion": "status", "expected": expected,
        "reqMethod": "POST", "reqUrl": url_part,
        "expectedConfirmed": True,
        "afterStepId": after,
        "url": "/popup", "pageId": page_id, "pageOrigin": origin,
    }


def _net_events(origin):
    """popup 里 #go 点击触发的写请求（录制 context 级抓到，无 page 归属）。"""
    return [
        {"id": 1, "t": 1700, "phase": "req", "method": "POST",
         "url": origin + "/api/save", "body": "p=1"},
        {"requestId": 1, "t": 1750, "phase": "res", "method": "POST",
         "url": origin + "/api/save", "status": 201},
    ]


def _run_spec(spec_text, page, tag):
    ns = {"__file__": str(Path(__file__))}
    exec(compile(spec_text, tag, "exec"), ns)
    return next(v for k, v in ns.items()
                if k.startswith("test_") and callable(v))


# ── 生成链：popup 请求的 count=0 必须数到 context 级请求 ───────────────────


class TestPopupCountZero:
    def test_popup_fired_request_fails_count_zero(self, popup_origin):
        """真 Chromium：popup 打开后点击 #go 发 POST → 在 popup 页录的
        count=0（afterStepId=popup 的 #go 点击，录制菜单的真实锚定）必须
        失败（actual=1）。修复前 watcher 只看 opener/动作所在 page，数不到
        popup 的请求 → 假绿（缺陷形态）。"""
        from generate_spec import generate_spec
        steps = [_open_popup_step(popup_origin),
                 _popup_click_step(popup_origin),
                 _count_zero_step(origin=popup_origin, after="act-2")]
        spec = generate_spec(steps, _net_events(popup_origin),
                             start_url=popup_origin + "/", name="pop")
        assert "rec-assert: rc-p1" in spec, spec
        # 产出的代码必须语法成立：同动作共存自动写断言（expect_request）
        # 与 count=0 时，观察者创建行落在 with 块内部 —— 缩进跟 with 走
        # （修复前缩进少一层，生成的 pytest 一执行就 IndentationError）
        compile(spec, "pop.py", "exec")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=popup_origin + "/")
            page = ctx.new_page()
            page.goto(popup_origin + "/")
            test_fn = _run_spec(spec, page, "pop.py")
            with pytest.raises(Exception) as ei:
                test_fn(page)
            msg = str(ei.value)
            assert "请求数" in msg or "request_count" in msg, (
                f"失败形态不对（应为负断言失败，而不是别的错）: "
                f"{type(ei.value).__name__}: {msg[:200]}")
            browser.close()

    def test_popup_quiet_passes_count_zero(self, popup_origin):
        """真 Chromium：popup 打开但不点 #go → 观察窗内无请求 → 通过。
        观察口径变大后这条仍然必须绿（context 级看，但没有匹配请求）。"""
        from generate_spec import generate_spec
        steps = [_open_popup_step(popup_origin),
                 _count_zero_step(origin=popup_origin)]
        spec = generate_spec(steps, [], start_url=popup_origin + "/",
                             name="pop-quiet")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=popup_origin + "/")
            page = ctx.new_page()
            page.goto(popup_origin + "/")
            test_fn = _run_spec(spec, page, "pop-quiet.py")
            test_fn(page)                       # 不抛 = 通过
            browser.close()


# ── trace 链：同一录制在回放链必须同判 ─────────────────────────────────────


class TestPopupTraceChain:
    def test_trace_replay_agrees_fired_request_fails(self, popup_origin):
        """trace 链对 popup 发出的请求同样必须失败 —— 两链同判。
        当前 replay_trace 只在 node_page 上建 watcher → trace 链假绿。"""
        from generate_spec import generate_spec
        from generate_trace import generate_trace
        from replay_trace import replay_trace
        steps = [_open_popup_step(popup_origin),
                 _popup_click_step(popup_origin),
                 _count_zero_step(origin=popup_origin, after="act-2")]
        net = _net_events(popup_origin)
        spec = generate_spec(steps, net, start_url=popup_origin + "/",
                             name="pop")
        trace = generate_trace(steps, net, name="pop",
                               start_url=popup_origin + "/")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=popup_origin + "/")
            page = ctx.new_page()
            page.goto(popup_origin + "/")
            # 生成链失败
            ns = {"__file__": str(__file__)}
            exec(compile(spec, "pop.py", "exec"), ns)
            test = next(v for k, v in ns.items()
                        if k.startswith("test_") and callable(v))
            with pytest.raises(Exception):
                test(page)
            # 回放链同样失败（当前实现：假绿）
            page2 = ctx.new_page()
            page2.goto(popup_origin + "/")
            execution = replay_trace(page2, trace, navigate=False,
                                     timeout_ms=6000)
            assert execution["status"] != "success", (
                "trace 链对 popup 发出的请求假绿（两链不同判）："
                + json.dumps(execution["steps"], ensure_ascii=False)[:400])
            browser.close()

    def test_trace_replay_quiet_passes(self, popup_origin):
        """popup 静默（不发请求）→ 两链都通过。"""
        from generate_trace import generate_trace
        from replay_trace import replay_trace
        steps = [_open_popup_step(popup_origin),
                 _count_zero_step(origin=popup_origin)]
        trace = generate_trace(steps, [], name="pop-quiet",
                               start_url=popup_origin + "/")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=popup_origin + "/")
            page = ctx.new_page()
            page.goto(popup_origin + "/")
            execution = replay_trace(page, trace, navigate=False,
                                     timeout_ms=6000)
            assert execution["status"] == "success", execution["steps"]
            browser.close()


# ── 一致性：录制步骤、spec、trace 三处对断言的归属一致 ─────────────────────


class TestPopupAttributionConsistency:
    def test_assertion_keeps_popup_page_id_in_both_chains(self, popup_origin):
        """scope=network 断言落在 popup 页上（pageId=p2）时，spec 与 trace
        都必须保留这个归属 —— 不能把断言悄悄挪到 opener 名下或丢弃。"""
        from generate_spec import generate_spec
        from generate_trace import generate_trace
        import trace_schema as ts
        steps = [_open_popup_step(popup_origin),
                 _popup_click_step(popup_origin),
                 _count_zero_step(origin=popup_origin)]
        spec = generate_spec(steps, [], start_url=popup_origin + "/",
                             name="pop-attr")
        # spec 拒发整份（断言的观察起点=因果动作在 opener 上，弹出页归属
        # 在 popup —— 当前语义允许这种情况通过 expect_request 配对处理，
        # 这里只要求断言必须真实存在，不能被静默丢掉）
        assert "rec-assert: rc-p1" in spec, spec
        trace = generate_trace(steps, [], name="pop-attr",
                               start_url=popup_origin + "/")
        found = [
            (ts.provenance(n).get("sourceStepId"), ts.assertion_of(n),
             ts.provenance(n).get("pageId"))
            for _, n in ts.nodes(trace) if ts.assertion_of(n)
        ]
        by_id = dict((sid, (a, pid)) for sid, a, pid in found)
        assert "rc-p1" in by_id, f"trace 丢了 count=0 断言: {found}"
        assertion, page_id = by_id["rc-p1"]
        assert assertion.get("scope") == "network"
        assert page_id == "p2", (
            f"断言归属被挪走：pageId={page_id!r}（录制时在 popup p2 上）")


# ── P2.8：popup 发起的**正向**网络断言（status / body）双链同判 ──────────────
#
# P2.7 只闭环了负断言（count=0）。正向缺口（quality-log 剩余风险 #2）：
# popup **加载时**发的请求（被 opener 的 #openload 点击因果引发），断言锚在
# opener 动作上；生成的 `page.expect_request` 绑 opener page，看不到 popup 的
# 请求 → 正向假红（请求明明 201，等却超时）。


def _open_popup_load_step(origin):
    """opener 点 #openload 打开加载即发请求的 popup（pageId=p1）。"""
    return {
        "id": "openload", "t": 1000, "type": "click",
        "sel": 'locator("#openload")', "kind": "css", "url": "/", "pageId": "p1",
        "pageOrigin": origin,
        "ui": {"templates": {"element": "openload.png"},
               "pageRect": {"x": 0, "y": 0, "width": 50, "height": 20},
               "pageViewport": {"width": 1280, "height": 720},
               "deviceScaleFactor": 1},
    }


def _load_net_events(origin):
    """popup 加载时发起的写请求（录制 context 级抓到，无 page 归属）。"""
    return [
        {"id": 7, "t": 1300, "phase": "req", "method": "POST",
         "url": origin + "/api/save", "body": '{"p": "1"}'},
        {"requestId": 7, "t": 1350, "phase": "res", "method": "POST",
         "url": origin + "/api/save", "status": 201},
    ]


def _load_status_step(origin, expected=201, after="openload"):
    """popup-load 请求的 status 断言，锚在 opener 的 openload 动作上（pageId=p1）。"""
    return {
        "id": "st-load", "t": 2600, "type": "assert", "scope": "network",
        "assertion": "status", "expected": expected,
        "reqMethod": "POST", "reqUrl": "/api/save",
        "expectedConfirmed": True, "afterStepId": after,
        "url": "/", "pageId": "p1", "pageOrigin": origin,
    }


def _load_body_step(origin, assertion, expected, after="openload", sid="bd-load"):
    return {
        "id": sid, "t": 2700, "type": "assert", "scope": "network",
        "assertion": assertion, "expected": expected,
        "reqMethod": "POST", "reqUrl": "/api/save",
        "expectedConfirmed": True, "afterStepId": after,
        "url": "/", "pageId": "p1", "pageOrigin": origin,
    }


def _both_outcomes(steps, net, origin, tag):
    """跑生成链与回放链，返回 (gen_ok, trace_ok)。各自独立新 context。

    SpecGenerationError 是「正向断言没有可绑定的请求记录」的诚实非通过（生成
    端拒发而不是发一份会假绿的代码）—— 对「无请求」用例它正是两条链都失败的
    合法形态，转成 (False, False) 而不是让上层误判为测试错误。
    """
    from generate_spec import generate_spec, SpecGenerationError
    from generate_trace import generate_trace
    from replay_trace import replay_trace
    try:
        spec = generate_spec(steps, net, start_url=origin + "/", name=tag)
        trace = generate_trace(steps, net, name=tag, start_url=origin + "/")
    except SpecGenerationError:
        return False, False
    gen_ok = trace_ok = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=resolve_chrome())
        # 生成链
        ctx = browser.new_context(base_url=origin + "/")
        page = ctx.new_page(); page.goto(origin + "/")
        try:
            _run_spec(spec, page, tag + ".py")(page)
            gen_ok = True
        except Exception:
            gen_ok = False
        ctx.close()
        # 回放链
        ctx2 = browser.new_context(base_url=origin + "/")
        page2 = ctx2.new_page(); page2.goto(origin + "/")
        execution = replay_trace(page2, trace, navigate=False, timeout_ms=6000)
        trace_ok = execution["status"] == "success"
        ctx2.close()
        browser.close()
    return gen_ok, trace_ok


class TestPopupPositiveNetworkAssert:
    """popup 加载时发出的请求，正向 status/body 断言必须在两条链都被抓到（不假红）。"""

    def test_pytest_chain_catches_popup_load_status(self, popup_origin):
        from generate_spec import generate_spec
        steps = [_open_popup_load_step(popup_origin),
                 _load_status_step(popup_origin, expected=201)]
        spec = generate_spec(steps, _load_net_events(popup_origin),
                             start_url=popup_origin + "/", name="pload")
        assert "rec-assert: st-load" in spec, spec
        compile(spec, "pload.py", "exec")          # 语法必须成立
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=popup_origin + "/")
            page = ctx.new_page(); page.goto(popup_origin + "/")
            try:
                _run_spec(spec, page, "pload.py")(page)
            except Exception as e:
                pytest.fail(
                    f"生成链对 popup-load 请求假红（请求明明 201 却没等到）: "
                    f"{type(e).__name__}: {str(e)[:160]}")
            browser.close()

    def test_popup_load_status_dual_chain_agree(self, popup_origin):
        """两链同判：popup-load 的正向 status 断言在 pytest 与 trace 都通过。"""
        steps = [_open_popup_load_step(popup_origin),
                 _load_status_step(popup_origin, expected=201)]
        gen_ok, trace_ok = _both_outcomes(
            steps, _load_net_events(popup_origin), popup_origin, "pload2")
        assert gen_ok is True, "生成链正向断言假红"
        assert trace_ok is True, "回放链正向断言假红"
        assert gen_ok == trace_ok

    def test_tampered_status_fails_both_chains(self, popup_origin):
        """反突变：把期望 201 篡改成 500，两条链都必须失败（不因松等待假绿）。"""
        steps = [_open_popup_load_step(popup_origin),
                 _load_status_step(popup_origin, expected=500)]
        gen_ok, trace_ok = _both_outcomes(
            steps, _load_net_events(popup_origin), popup_origin, "ptam")
        assert gen_ok is False, "生成链对篡改过的 status 假绿"
        assert trace_ok is False, "回放链对篡改过的 status 假绿"

    def test_popup_load_body_subset_dual_chain(self, popup_origin):
        """正向 request_body / response_body subset 在两条链同判（正例通过）。"""
        steps = [_open_popup_load_step(popup_origin),
                 _load_body_step(popup_origin, "request_body", {"p": "1"}),
                 _load_body_step(popup_origin, "response_body", {}, sid="bd2")]
        gen_ok, trace_ok = _both_outcomes(
            steps, _load_net_events(popup_origin), popup_origin, "pbody")
        assert gen_ok is True, f"生成链 body 断言失败: "
        assert trace_ok is True, "回放链 body 断言失败"
        assert gen_ok == trace_ok

    def test_absent_request_fails_both_chains_not_false_pass(self, popup_origin):
        """读取异常不能假通过：popup 的请求始终不发（无 net）时，status 断言在
        两条链都必须失败 —— 不能因为观察不到就把「期望的状态」当成满足。"""
        steps = [_open_popup_load_step(popup_origin),
                 _load_status_step(popup_origin, expected=201)]
        gen_ok, trace_ok = _both_outcomes(
            steps, [], popup_origin, "pnone")   # 无请求事件
        assert gen_ok is False, "生成链在无请求时报绿（假通过）"
        assert trace_ok is False, "回放链在无请求时报绿（假通过）"
