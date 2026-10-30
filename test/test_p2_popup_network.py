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
            if self.path.startswith("/popup"):
                body = (b"<!doctype html><html><head><meta charset='utf-8'>"
                        b"<title>popup</title></head><body>"
                        b"<button id='go' onclick=\"setTimeout(function(){"
                        b"fetch('/api/save',{method:'POST',body:'p=1'})},250)\">"
                        b"popup go</button><div id='marker'>p</div></body></html>")
            else:
                body = (b"<!doctype html><html><head><meta charset='utf-8'>"
                        b"<title>opener</title></head><body>"
                        b"<a id='open' href='/popup' target='_blank'>open popup</a>"
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
