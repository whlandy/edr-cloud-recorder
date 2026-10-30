"""P2.6 request_count=0 有界观察窗 真实闭环失败测试（common-web-assertion-design §9.2 item 3）。

矩阵缺口（连续四轮遗留）：request_count=0 只有单元证据 ——
- 生成链产出的代码引用 `_net_watcher`，但模板从未定义它、HEADER 未导入
  `request_count_zero`，也没有传观察窗参数 —— 生成的 pytest 一执行就 NameError。
- 观察窗语义要求「动作刚发生时不能立即判 0」（请求可能还在路上）。sync
  Playwright 里 `page.on("request")` 回调只在事件循环被驱动时分发 ——
  `time.sleep` 轮询不驱动事件分发，观察者数恒 0，负断言假绿（本文件用
  真实 Chromium 证明）。
- trace 链 `_merge_user_network_expectation` 没有 request_count 分支：断言
  要么被静默丢掉（一致性门禁必然拒绝真实录制产物），要么被当成「必有
  响应」的等待（语义恰好相反）。

先写测试：每条先失败，再做最小实现。
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from chrome_path import resolve_chrome

# ── 本地 HTTP fixture：/ 页面按钮点击后延迟 300ms 才发 POST /api/save ────────
# 延迟是关键：观察窗语义要求「动作刚发生时不能立即判 0」——
# 点击后立刻读计数是 0（请求还没发），300ms 后请求到达。轮询必须能
# 看到它到达（事件分发被驱动），否则负断言在 300ms 稳定窗内假绿。


def _start_server(*, delay_ms: int = 300, fire: bool = True):
    onclick = (
        f"setTimeout(function(){{fetch('/api/save',{{method:'POST'}})}},"
        f"{delay_ms})"
        if fire else "void 0"
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = (b"<!doctype html><html><body>"
                    b"<button id='go' onclick=\"" + onclick.encode() + b"\">go</button>"
                    b"<div id='marker'>x</div></body></html>")
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
def api_origin():
    srv, origin = _start_server()
    try:
        yield origin
    finally:
        srv.shutdown()


@pytest.fixture(scope="module")
def quiet_origin():
    srv, origin = _start_server(fire=False)
    try:
        yield origin
    finally:
        srv.shutdown()


# ── 步骤构造（与 test_p2_network_scope 同款）────────────────────────────────


def _click_step(origin):
    return {
        "id": "act-1", "t": 1000, "type": "click",
        "sel": 'getByRole("button", { name: "go", exact: true })', "kind": "role",
        "url": "/", "pageId": "p1", "pageOrigin": origin,
        "ui": {"templates": {"element": "act-1.png"},
               "pageRect": {"x": 0, "y": 0, "width": 50, "height": 20},
               "pageViewport": {"width": 1280, "height": 720},
               "deviceScaleFactor": 1},
    }


def _count_zero_step(step_id="rc-1", confirmed=True, after="act-1",
                     origin="http://x", url_part="/api/save", expected=0):
    return {
        "id": step_id, "t": 2000, "type": "assert", "scope": "network",
        "assertion": "request_count", "expected": expected,
        "reqMethod": "POST", "reqUrl": url_part,
        "expectedConfirmed": confirmed,
        "afterStepId": after,
        "url": "/", "pageId": "p1", "pageOrigin": origin,
        "wait": {"timeoutMs": 2000, "pollMs": 50, "stableMs": 300},
    }


def _run_spec(spec_text, page, tag):
    ns = {"__file__": str(Path(__file__))}
    exec(compile(spec_text, tag, "exec"), ns)
    return next(v for k, v in ns.items()
                if k.startswith("test_") and callable(v))


# ── 生成链：产出的代码必须可执行，且能看见迟到的请求 ────────────────────────


class TestGenerateCountZero:
    def test_generated_code_defines_watcher_and_runs(self, api_origin):
        """request_count=0 断言产出的代码必须真实可执行 —— 观察者已定义、
        已导入、观察窗参数齐全。当前实现引用未定义的 `_net_watcher`，
        exec 时 NameError（连代码都不成立，谈不上断言）。"""
        from generate_spec import generate_spec
        steps = [_click_step(api_origin),
                 _count_zero_step(origin=api_origin)]
        spec = generate_spec(steps, [], start_url=api_origin + "/", name="rc")
        assert "rec-assert: rc-1" in spec, spec
        assert "request_count_zero" in spec, spec
        # 观察者必须由模板定义，不能引用不存在的名字
        assert "_net_watcher" in spec
        watcher_def = [ln for ln in spec.splitlines()
                       if re.match(r"^ *_net_watcher\w* = ", ln)]
        assert watcher_def, (
            "spec 引用 _net_watcher 但从未定义它：\n" + spec)
        # 观察窗语义：不能立即判 0 —— 稳定窗必须传进断言调用
        call = [ln for ln in spec.splitlines() if "request_count_zero(" in ln]
        assert call and "stable" in call[0], (
            f"request_count_zero 调用缺稳定窗参数: {call}")

    def test_count_zero_generated_code_executes_and_sees_late_request(
            self, api_origin):
        """真 Chromium：点击后 300ms 请求才发出。观察窗轮询必须驱动事件
        分发、捕获这条迟到请求，负断言明确失败 —— 而不是数恒 0 假绿。"""
        from generate_spec import generate_spec
        steps = [_click_step(api_origin),
                 _count_zero_step(origin=api_origin)]
        spec = generate_spec(steps, [], start_url=api_origin + "/", name="rc")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=api_origin + "/")
            page = ctx.new_page()
            page.goto(api_origin + "/")
            test_fn = _run_spec(spec, page, "rc.py")
            with pytest.raises(Exception) as ei:
                test_fn(page)
            # 失败必须是「数到了请求」的负断言失败，不是 NameError/超时
            msg = str(ei.value)
            assert "请求数" in msg or "request_count" in msg, (
                f"失败形态不对（应为负断言失败）: {type(ei.value).__name__}: {msg[:200]}")
            browser.close()

    def test_count_zero_passes_when_no_request_fired(self, quiet_origin):
        """真 Chromium：动作没发匹配请求 → 观察窗走满稳定窗后通过。"""
        from generate_spec import generate_spec
        steps = [_click_step(quiet_origin),
                 _count_zero_step(origin=quiet_origin)]
        spec = generate_spec(steps, [], start_url=quiet_origin + "/", name="rc")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=quiet_origin + "/")
            page = ctx.new_page()
            page.goto(quiet_origin + "/")
            test_fn = _run_spec(spec, page, "rc.py")
            test_fn(page)                       # 不抛 = 通过
            browser.close()


# ── trace 链：request_count 期望必须落到回放可验证的形态 ────────────────────


class TestTraceCountZero:
    def test_trace_carries_count_zero_expectation(self, api_origin):
        """trace 必须携带 request_count=0 的可验证期望。当前实现静默丢弃
        （_merge_user_network_expectation 无 request_count 分支）→ 断言在
        trace 里没有对应校验，一致性门禁也必然拒绝真实录制产物。"""
        from generate_trace import generate_trace
        import trace_schema as ts
        steps = [_click_step(api_origin),
                 _count_zero_step(origin=api_origin)]
        trace = generate_trace(steps, [], name="rc", start_url=api_origin + "/")
        found = [
            (ts.provenance(n).get("sourceStepId"), ts.assertion_of(n))
            for _, n in ts.nodes(trace)
            if ts.assertion_of(n)
        ]
        by_id = dict(found)
        assert "rc-1" in by_id, f"request_count 断言丢失: {found}"
        assert by_id["rc-1"].get("assertion") == "request_count"
        # 回放链必须能按这个期望验证（计数窗口），不只是挂个名字
        node = next(n for sid, _ in found if sid == "rc-1" for n in [dict(_)])
        assert by_id["rc-1"].get("expectedConfirmed") is not False

    def test_two_paths_agree_count_zero_positive_and_negative(self, api_origin,
                                                              quiet_origin):
        """两链同判：正向（不发请求的服务端）通过；反向（有迟到请求）
        双链明确失败，失败信息能指回这条断言（rec-assert id / 请求数语义）。"""
        from generate_spec import generate_spec
        from generate_trace import generate_trace
        from replay_trace import replay_trace
        steps = [_click_step(api_origin),
                 _count_zero_step(origin=api_origin)]
        spec = generate_spec(steps, [], start_url=api_origin + "/", name="rc")
        trace = generate_trace(steps, [], name="rc", start_url=api_origin + "/")
        quiet_trace = generate_trace(
            [_click_step(quiet_origin), _count_zero_step(origin=quiet_origin)],
            [], name="rc", start_url=quiet_origin + "/")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            # 生成链 spec 里 page.goto("/") 是相对地址，靠 context.base_url
            # 落到对应 origin —— 正反两链各自的 context。
            # 反向：真实页面点了会发请求 —— 双链都必须失败
            ctx = browser.new_context(base_url=api_origin + "/")
            page = ctx.new_page()
            page.goto(api_origin + "/")
            test_fn = _run_spec(spec, page, "rc.py")
            with pytest.raises(Exception):
                test_fn(page)
            exec_bad = replay_trace(page, trace, navigate=False, timeout_ms=4000)
            assert exec_bad["status"] != "success", exec_bad["steps"]
            # 正向：服务端不触发请求的页面 —— 双链都必须通过。
            # （生成链会先 page.goto("/")，所以「关掉请求」必须在服务端做，
            # 页面内补丁会被 goto 重置。）
            ctx2 = browser.new_context(base_url=quiet_origin + "/")
            page2 = ctx2.new_page()
            page2.goto(quiet_origin + "/")
            test_fn2 = _run_spec(
                generate_spec(
                    [_click_step(quiet_origin),
                     _count_zero_step(origin=quiet_origin)],
                    [], start_url=quiet_origin + "/", name="rc"),
                page2, "rc.py")
            test_fn2(page2)
            exec_ok = replay_trace(page2, quiet_trace, navigate=False,
                                   timeout_ms=4000)
            assert exec_ok["status"] == "success", exec_ok["steps"]
            browser.close()


# ── 因果归属：afterStepId 指向不存在的动作 → 拒发，不许静默丢 ───────────────


class TestCountZeroCausality:
    def test_trace_rejects_orphan_causality(self, api_origin):
        """afterStepId 指向不存在/被丢弃的步骤 → generate_trace 必须拒绝。
        generate_spec 已拒发；trace 链当前静默丢断言（双链不同判的另一个
        来源 —— 同一份录制一条链报错、另一条链少断言）。"""
        from generate_spec import SpecGenerationError, generate_spec
        from generate_trace import generate_trace
        steps = [_click_step(api_origin),
                 _count_zero_step(origin=api_origin, after="act-gone")]
        with pytest.raises(SpecGenerationError):
            generate_spec(steps, [], start_url=api_origin + "/", name="rc")
        with pytest.raises(Exception) as ei:
            generate_trace(steps, [], name="rc", start_url=api_origin + "/")
        assert "act-gone" in str(ei.value), str(ei.value)
