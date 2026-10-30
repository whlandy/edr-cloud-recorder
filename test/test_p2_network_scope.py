"""P2.2 网络范围断言（scope=network）失败测试（common-web-assertion-design §9.2）。

用户在录制菜单选「最近请求」范围，对触发动作发出的请求做契约断言：
status / request_body subset / response_body subset / request_count=0。
先写测试：每条先失败，再做最小实现。落点：

* generate_spec：scope=network 的断言编译进因果动作的 pre-armed
  expect_request 等待块（与自动写请求断言同一路径，按 request id 配对）；
  不可转译（无动作归属、坏 op、未确认 expected）拒发整份 spec。
* replay_trace：scope=network 节点在回放链按同一语义执行（响应等完再断）。
* 一致性：recording / pytest / trace 三处断言 id 一致（复用现有 P0 机制）。
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from playwright.sync_api import sync_playwright

from chrome_path import resolve_chrome

# ── 本地 HTTP fixture：POST /api/save 返回 JSON，GET /api/list 供 count=0 场景 ──

API_RESPONSE = {"ok": True, "saved": True, "updatedAt": "2026-10-30T00:00:00Z"}


def _start_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"<!doctype html><html><body><button id='go' onclick=\"fetch('/api/save',{method:'POST',body:JSON.stringify({x:1})})\">go</button></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.dumps(API_RESPONSE).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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


# ── 步骤构造 ────────────────────────────────────────────────────────────────

def _network_step(step_id, assertion, expected, *, op=None, method="POST",
                  url_part="/api/save", confirmed=True, after="act-1",
                  origin="http://x", extra=None):
    step = {
        "id": step_id, "t": 2000, "type": "assert", "scope": "network",
        "assertion": assertion, "expected": expected,
        "reqMethod": method, "reqUrl": url_part,
        "expectedConfirmed": confirmed,
        "afterStepId": after,
        "url": "/", "pageId": "p1", "pageOrigin": origin,
    }
    if op:
        step["op"] = op
    if extra:
        step.update(extra)
    return step


def _click_step(origin="http://x"):
    # 带 fake 模板让 trace 达到 ready（回放走 DOM 定位，模板仅视觉兜底）。
    # 与 test_acceptance_p1_scenarios._click 同款。
    return {
        "id": "act-1", "t": 1000, "type": "click",
        "sel": 'getByRole("button", { name: "go", exact: true })', "kind": "role",
        "url": "/", "pageId": "p1", "pageOrigin": origin,
        "ui": {"templates": {"element": "act-1.png"},
               "pageRect": {"x": 0, "y": 0, "width": 50, "height": 20},
               "pageViewport": {"width": 1280, "height": 720},
               "deviceScaleFactor": 1},
    }


# ── 生成链：scope=network 编译进因果动作的等待块 ────────────────────────────


class TestGenerateNetworkScope:
    def test_status_assert_compiles_into_action_wait(self, api_origin):
        """scope=network status 断言编译为因果动作 expect_request 块上的状态断言。"""
        from generate_spec import generate_spec
        steps = [_click_step(),
                 _network_step("net-1", "status", 201)]
        net = [
            {"id": 1, "t": 1050, "phase": "req", "method": "POST",
             "url": api_origin + "/api/save", "body": '{"x":1}'},
            {"requestId": 1, "t": 1100, "phase": "res", "method": "POST",
             "url": api_origin + "/api/save", "status": 201},
        ]
        spec = generate_spec(steps, net, start_url=api_origin + "/", name="net")
        # 断言必须真实存在（不能只是注释），且带上录制 id 标记
        assert "rec-assert: net-1" in spec
        marker = spec.split("rec-assert: net-1")[0]
        assert "resp.status == 201" in marker or "status == 201" in marker

    def test_request_body_subset_compiles(self, api_origin):
        from generate_spec import generate_spec
        steps = [_click_step(),
                 _network_step("net-2", "request_body", {"x": 1})]
        net = [
            {"id": 1, "t": 1050, "phase": "req", "method": "POST",
             "url": api_origin + "/api/save", "body": '{"x":1}'},
            {"requestId": 1, "t": 1100, "phase": "res", "method": "POST",
             "url": api_origin + "/api/save", "status": 201},
        ]
        spec = generate_spec(steps, net, start_url=api_origin + "/", name="net")
        assert "rec-assert: net-2" in spec
        marker = spec.split("rec-assert: net-2")[0]
        assert "assert_subset" in marker and "post_data_json" in marker

    def test_response_body_subset_compiles(self, api_origin):
        from generate_spec import generate_spec
        steps = [_click_step(),
                 _network_step("net-3", "response_body", {"ok": True})]
        net = [
            {"id": 1, "t": 1050, "phase": "req", "method": "POST",
             "url": api_origin + "/api/save", "body": '{"x":1}'},
            {"requestId": 1, "t": 1100, "phase": "res", "method": "POST",
             "url": api_origin + "/api/save", "status": 201, "body": '{"ok":true}'},
        ]
        spec = generate_spec(steps, net, start_url=api_origin + "/", name="net")
        assert "rec-assert: net-3" in spec
        marker = spec.split("rec-assert: net-3")[0]
        assert "assert_subset" in marker and ".json()" in marker

    def test_unconfirmed_expected_rejects_whole_spec(self, api_origin):
        """expectedConfirmed=false 的网络断言拒发整份 spec（P0 语义）。"""
        from generate_spec import SpecGenerationError, generate_spec
        steps = [_click_step(),
                 _network_step("net-4", "status", 201, confirmed=False)]
        net = [
            {"id": 1, "t": 1050, "phase": "req", "method": "POST",
             "url": api_origin + "/api/save"},
            {"requestId": 1, "t": 1100, "phase": "res", "method": "POST",
             "url": api_origin + "/api/save", "status": 201},
        ]
        with pytest.raises(SpecGenerationError) as ei:
            generate_spec(steps, net, start_url=api_origin + "/", name="net")
        assert "net-4" in str(ei.value)

    def test_network_assert_without_causal_action_rejects(self, api_origin):
        """没有 afterStepId 指向的动作步骤 → 无法确定观察起点，拒发。"""
        from generate_spec import SpecGenerationError, generate_spec
        steps = [_network_step("net-5", "status", 201, after=None)]
        net = [
            {"id": 1, "t": 1050, "phase": "req", "method": "POST",
             "url": api_origin + "/api/save"},
            {"requestId": 1, "t": 1100, "phase": "res", "method": "POST",
             "url": api_origin + "/api/save", "status": 201},
        ]
        with pytest.raises(SpecGenerationError) as ei:
            generate_spec(steps, net, start_url=api_origin + "/", name="net")
        assert "net-5" in str(ei.value)


# ── 回放链：scope=network 两链同判 ─────────────────────────────────────────


class TestReplayNetworkScope:
    def test_trace_carries_network_assertion(self, api_origin):
        from generate_trace import generate_trace
        steps = [_click_step(),
                 _network_step("net-6", "status", 201)]
        trace = generate_trace(steps, [], name="net",
                               start_url=api_origin + "/")
        import trace_schema as ts
        found = [
            (ts.provenance(n).get("sourceStepId"), ts.assertion_of(n))
            for _, n in ts.nodes(trace)
            if ts.assertion_of(n)
        ]
        by_id = dict(found)
        assert "net-6" in by_id
        assert by_id["net-6"]["scope"] == "network"

    def test_two_paths_agree_status(self, api_origin):
        """status 网络断言在生成链（exec pytest）与回放链都通过。"""
        from generate_spec import generate_spec
        from generate_trace import generate_trace
        from replay_trace import replay_trace

        steps = [_click_step(api_origin),
                 _network_step("net-7", "status", 201, origin=api_origin)]
        net = [
            {"id": 1, "t": 1050, "phase": "req", "method": "POST",
             "url": api_origin + "/api/save"},
            {"requestId": 1, "t": 1100, "phase": "res", "method": "POST",
             "url": api_origin + "/api/save", "status": 201},
        ]
        spec = generate_spec(steps, net, start_url=api_origin + "/", name="net")
        trace = generate_trace(steps, net, name="net",
                               start_url=api_origin + "/")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=api_origin + "/")
            page = ctx.new_page()
            page.goto(api_origin + "/")
            # 生成链
            ns = {"__file__": str(__file__)}
            exec(compile(spec, "net.py", "exec"), ns)
            test = next(v for k, v in ns.items()
                        if k.startswith("test_") and callable(v))
            test(page)
            # 回放链
            execution = replay_trace(page, trace, navigate=False,
                                     timeout_ms=3000)
            assert execution["status"] == "success", execution["steps"]
            browser.close()

    def test_two_paths_agree_status_tampered_fails(self, api_origin):
        """主动篡改 expected（201→200）后两条链都必须明确失败。"""
        from generate_spec import generate_spec
        from generate_trace import generate_trace
        from replay_trace import replay_trace

        steps = [_click_step(api_origin),
                 _network_step("net-8", "status", 200, origin=api_origin)]   # 篡改的期望
        net = [
            {"id": 1, "t": 1050, "phase": "req", "method": "POST",
             "url": api_origin + "/api/save"},
            {"requestId": 1, "t": 1100, "phase": "res", "method": "POST",
             "url": api_origin + "/api/save", "status": 201},
        ]
        spec = generate_spec(steps, net, start_url=api_origin + "/", name="net")
        trace = generate_trace(steps, net, name="net",
                               start_url=api_origin + "/")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=api_origin + "/")
            page = ctx.new_page()
            page.goto(api_origin + "/")
            # 生成链：必须失败
            ns = {"__file__": str(__file__)}
            exec(compile(spec, "net.py", "exec"), ns)
            test = next(v for k, v in ns.items()
                        if k.startswith("test_") and callable(v))
            with pytest.raises(Exception):
                test(page)
            # 回放链：同样失败
            execution = replay_trace(page, trace, navigate=False,
                                     timeout_ms=3000)
            assert execution["status"] != "success"
            browser.close()
