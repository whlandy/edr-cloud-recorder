"""P2 页面与网络契约断言（common-web-assertion-design.md §9.2）失败测试。

先写测试：每条先失败，再做最小实现。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from chrome_path import resolve_chrome

P2_HTML = "<!doctype html><html><head><meta charset='utf-8'><title>p2</title></head><body><h1>p2</h1></body></html>"


def _start_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = P2_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


@pytest.fixture(scope="module")
def reachable_origin():
    srv, origin = _start_server()
    try:
        yield origin
    finally:
        srv.shutdown()


# ── P2.1: page_count（等于/至少/至多，统计当前 browser context 的 page 数） ────


def test_expect_page_count_helper():
    """expect_page_count 轮询 page.context.pages 的数量直到满足算子。"""
    import rec_assert

    class _Context:
        def __init__(self, n):
            self._n = n

        @property
        def pages(self):
            return list(range(self._n))

    class _Page:
        def __init__(self, n):
            self.context = _Context(n)

    pg = _Page(2)
    assert rec_assert.expect_page_count(pg, 2, "equals", timeout=1.0) == 2


def test_expect_page_count_ops():
    import rec_assert

    class _Context:
        def __init__(self, n):
            self._n = n

        @property
        def pages(self):
            return list(range(self._n))

    class _Page:
        def __init__(self, n):
            self.context = _Context(n)

    # at_least / at_most / equals
    assert rec_assert.expect_page_count(_Page(3), 3, "equals", timeout=1.0) == 3
    assert rec_assert.expect_page_count(_Page(3), 1, "at_least", timeout=1.0) == 3
    assert rec_assert.expect_page_count(_Page(3), 5, "at_most", timeout=1.0) == 3
    # 不满足 → 超时失败（不假通过）
    with pytest.raises(AssertionError):
        rec_assert.expect_page_count(_Page(1), 5, "at_least", timeout=0.0)


def test_page_count_read_failure_is_not_observation():
    """读取 page.context.pages 抛错不是观测值，不得用它假通过。"""
    import rec_assert

    class _Context:
        @property
        def pages(self):
            raise RuntimeError("context detached")

    class _Page:
        uri = "http://x"

        @property
        def context(self):
            return _Context()

    with pytest.raises(AssertionError, match="读取持续失败"):
        rec_assert.expect_page_count(_Page(), 1, "equals", timeout=0.0)


# ── P2.2: 最近请求选择器 + status/request_body/response_body subset ──────────


class _Req:
    def __init__(self, method, url, body):
        self.method = method
        self.url = url
        self._body = body

    @property
    def post_data_json(self):
        return self._body


class _Resp:
    def __init__(self, status, body, req):
        self.status = status
        self._text = body
        self.request = req
        self.url = req.url

    def text(self):
        return self._text


def test_response_status_subset():
    """response status 匹配 expected status；不匹配则失败。"""
    import rec_assert
    resp = _Resp(200, "{}", _Req("POST", "http://x/api", {"a": 1}))
    assert rec_assert.expect_response_status(resp, 200) == 200
    with pytest.raises(AssertionError):
        rec_assert.expect_response_status(resp, 400)


def test_response_body_subset():
    """response/request body 用 subset 语义匹配。"""
    import rec_assert
    resp = _Resp(200, json.dumps({"code": "200", "data": {"id": 1}}),
                 _Req("POST", "http://x/api", {"a": 1, "b": 2}))
    # 请求体 subset
    assert rec_assert.matches_body_subset(resp.request.post_data_json, {"a": 1}) is True
    assert rec_assert.matches_body_subset(resp.request.post_data_json, {"a": 9}) is False
    # 响应体 subset
    assert rec_assert.matches_body_subset(json.loads(resp.text()), {"code": "200"}) is True
    assert rec_assert.matches_body_subset(json.loads(resp.text()), {"code": "500"}) is False


# ── P2.3: request_count=0 有界观察窗 ──────────────────────────────────────────


class _WindowWatcher:
    """在 [start, end) 时间窗内统计某 URL 的请求数。"""

    def __init__(self, start, end, requests):
        self.start = start
        self.end = end
        self.requests = requests  # list of (t, url)

    def count(self, url_filter):
        return sum(1 for t, u in self.requests if self.start <= t < self.end
                   and (url_filter is None or url_filter in u))


def test_request_count_zero_bounded_window():
    """request_count=0 必须限定观察窗 —— 动作刚发生时不能立即判 0（此时请求
    可能还在路上），也不能同窗内已有同 URL 请求后误当“没发”。
    这里用稳定窗 + 边界过滤验证：窗外的自我请求不算。
    """
    import rec_assert
    # 在窗 [10, 30) 内没有任何 /api/save → count 0 通过
    watcher = _WindowWatcher(10, 30, [(5, "/api/save"), (40, "/api/save")])
    assert rec_assert.request_count_zero(
        watcher, "/api/save", 10, 30, stable=0.2) is True
    # 窗内有 /api/save → count 0 必须失败（不能假通过）
    watcher2 = _WindowWatcher(10, 30, [(15, "/api/save")])
    with pytest.raises(AssertionError):
        rec_assert.request_count_zero(watcher2, "/api/save", 10, 30, stable=0.2)


# ── P2.4: 网络-触发按 request id / 时间窗关联 ─────────────────────────────────


def test_pair_recent_request_by_id_not_last_same_url():
    """最近请求选择器必须按 request id 可靠配对，不能靠「最后一个同 URL 请求」猜。

    同一操作并发发两份同 URL 请求时，用 id 匹配响应与请求；「最后一条同 URL」
    会错配。这里验证 generate_spec 的 pair_network_events id 优先路径在并发
    同 URL 下仍一一对应。"""
    from generate_spec import pair_network_events
    net = [
        {"id": 1, "t": 1, "phase": "req", "method": "POST", "url": "http://x/save"},
        {"id": 2, "t": 2, "phase": "req", "method": "POST", "url": "http://x/save"},
        {"requestId": 2, "t": 3, "phase": "res", "method": "POST",
         "url": "http://x/save", "status": 200},
        {"requestId": 1, "t": 4, "phase": "res", "method": "POST",
         "url": "http://x/save", "status": 201},
    ]
    pairs = pair_network_events(net)
    # 两条都要配对成功，且响应按 requestId 对应：id1→201, id2→200
    matched = {(r["id"] if r else None, res["status"]) for r, res in pairs if r}
    assert (1, 201) in matched and (2, 200) in matched
    assert len(pairs) == 2


# ── P2.1 两链同判：page_count（真实 Chromium，双页面） ─────────────────────────


def _page_count_step(origin, expected, op="equals"):
    return {
        "id": "pc", "t": 1, "type": "assert", "scope": "page",
        "assertion": "page_count", "expected": expected, "op": op,
        "expectedConfirmed": True,
        "url": "/", "pageOrigin": origin, "pageId": "p1",
    }


def test_page_count_two_paths_agree(reachable_origin):
    """生成链（exec pytest）与回放链（replay_trace）对 page_count 同判。"""
    from generate_spec import generate_spec
    from generate_trace import generate_trace
    from replay_trace import replay_trace

    steps = [_page_count_step(reachable_origin, 2, "equals")]
    spec = generate_spec(steps, [], start_url=reachable_origin + "/", name="pc")
    trace = generate_trace(steps, [], name="pc", start_url=reachable_origin + "/")
    # 生成链应当产出 expect_page_count
    assert "expect_page_count(" in spec

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=resolve_chrome())
        ctx = browser.new_context(base_url=reachable_origin + "/")
        p1 = ctx.new_page()
        p1.goto(reachable_origin + "/")
        p2 = ctx.new_page()
        p2.goto(reachable_origin + "/")
        # 生成链
        ns = {"__file__": str(Path(__file__))}
        exec(compile(spec, "pc.py", "exec"), ns)
        test = next(v for k, v in ns.items() if k.startswith("test_") and callable(v))
        test(p1)
        # 回放链
        execution = replay_trace(p1, trace, navigate=False, timeout_ms=2000)
        assert execution["status"] == "success", execution["steps"]
        browser.close()
