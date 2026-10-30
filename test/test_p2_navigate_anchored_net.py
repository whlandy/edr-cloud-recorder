"""P2.12 生成门不对称（count=0 锚在 navigate 步骤上）：先失败测试。

场景（quality-log P2.11 剩余风险 #2）：用户在录制里先 reload 页面（或前进
后退），紧跟着下一条「不应发请求」网络断言 —— 断言菜单的 afterStepId 绑
「最近一步」，navigate 步骤就是最近一步，这个组合真实可达：

- generate_spec 的 navigate 分支只 emit page.reload()/page.go_back()，从不
  领走 _network_by_action 里挂在自己名下的断言 → leftover 检查拒发整份
  spec（真实复现：网络断言 (id=rc-1) 的因果动作步骤 'nav-1' 不在生成后的
  步骤序列里）。
- generate_trace 的 Navigate 是独立节点，count=0 观察者照常预扫 → 接受。

同一份录制一条链报错、另一条链产出可回放的 trace —— 生成门不对称，双链
不同判（test_p2_nav_pacing 同族，但缝在生成期而不是回放期）。

先写测试：每条先失败（T1/T3 红），再做最小修复（navigate 分支走 emit_action
领走自己名下的网络断言）。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from chrome_path import resolve_chrome

ORIGIN_T = "http://127.0.0.1:1"


def _start_server(*, on_load_post: bool):
    """on_load_post=True：页面**每次加载**（含 reload 后的新文档）发
    POST /api/save —— reload 的观察窗抓的就是新文档发出的请求；
    setTimeout 定时器会被 reload 抹掉，不能当负向触发器（实测假绿）。
    False：页面完全静默（正向）。"""

    load_js = (b"<script>fetch('/api/save',{method:'POST',body:'x=1'})"
               b"</script>" if on_load_post else b"")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = (b"<!doctype html><html><body>" + load_js +
                    b"<button id='go'>go</button>"
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
def quiet_origin():
    srv, origin = _start_server(on_load_post=False)
    try:
        yield origin
    finally:
        srv.shutdown()


@pytest.fixture(scope="module")
def api_origin():
    srv, origin = _start_server(on_load_post=True)
    try:
        yield origin
    finally:
        srv.shutdown()


def _steps(origin):
    """click → navigate(reload) → count=0 锚在 navigate 步骤上。"""
    return [
        {"id": "act-1", "t": 1000, "type": "click",
         "sel": 'getByRole("button", { name: "go", exact: true })',
         "kind": "role", "url": "/", "pageId": "p1", "pageOrigin": origin,
         "ui": {"templates": {"element": "act-1.png"},
                "pageRect": {"x": 0, "y": 0, "width": 50, "height": 20},
                "pageViewport": {"width": 1280, "height": 720},
                "deviceScaleFactor": 1}},
        {"id": "nav-1", "t": 1500, "type": "navigate", "navType": "reload",
         "url": "/", "pageId": "p1", "pageOrigin": origin},
        {"id": "rc-1", "t": 2000, "type": "assert", "scope": "network",
         "assertion": "request_count", "expected": 0,
         "reqMethod": "POST", "reqUrl": origin + "/api/save",
         "expectedConfirmed": True, "afterStepId": "nav-1",
         "url": "/", "pageId": "p1", "pageOrigin": origin,
         "wait": {"timeoutMs": 2000, "pollMs": 50, "stableMs": 300}},
    ]


def _run_spec(spec_text, page, tag):
    ns = {"__file__": str(Path(__file__))}
    exec(compile(spec_text, tag, "exec"), ns)
    return next(v for k, v in ns.items()
                if k.startswith("test_") and callable(v))


class TestNetworkAssertRequiresCausality:
    def test_both_gates_reject_missing_after_step_id(self, api_origin):
        """T4 真实录制闭环暴露的第二道缝：菜单绑「最近一步」，但注入器的
        navigate 步骤上报从不更新 lastStepId —— reload 后立刻下断言时
        afterStepId 丢失。generate_spec 已拒发（afterStepId 缺失 → 无法确定
        观察起点）；generate_trace 却接受（F6 只校验存在的锚），回放链
        count=0 预扫描 src_to_node[None] 直接 KeyError，status/body 断言被
        静默丢弃 —— 同一录制两链不同判的另一个来源。两链都必须拒绝。"""
        from generate_spec import SpecGenerationError, generate_spec
        from generate_trace import generate_trace
        steps = [s for s in _steps(api_origin) if s["id"] != "nav-1"]
        assert steps[1].get("afterStepId") == "nav-1"
        steps[1].pop("afterStepId")
        with pytest.raises(SpecGenerationError):
            generate_spec(steps, [], start_url=api_origin + "/", name="navz")
        with pytest.raises(Exception) as ei:
            generate_trace(steps, [], name="navz", start_url=api_origin + "/")
        assert "afterStepId" in str(ei.value), str(ei.value)


class TestNavigateAnchoredCountZero:
    def test_both_gates_accept_same_recording(self, api_origin):
        """T1 生成门对称：同一份录制（count=0 锚在 reload 上）两条链都接受。
        当前 generate_spec 拒发整份 spec、generate_trace 接受 —— 不对称红。"""
        from generate_spec import generate_spec
        from generate_trace import generate_trace
        steps = _steps(api_origin)
        spec = generate_spec(steps, [], start_url=api_origin + "/", name="navz")
        assert "rec-assert: rc-1" in spec, (
            "spec 接受了录制却丢了断言：\n" + spec)
        trace = generate_trace(steps, [], name="navz",
                               start_url=api_origin + "/")
        import trace_schema as ts
        assert any(
            (ts.provenance(n).get("sourceStepId") == "rc-1")
            for _, n in ts.nodes(trace) if ts.assertion_of(n))

    def test_generated_code_runs_and_watches_window(self, quiet_origin):
        """T2 生成链真实可执行：观察窗锚在 reload 后。观察者必须赶在
        reload（因果动作）前创建，稳定窗轮询在动作后 —— 页面 reload 不发
        POST 时通过；把页面换成会发的环境时必须抓住（与 click 锚语义同构）。"""
        from generate_spec import generate_spec
        steps = _steps(quiet_origin)
        spec = generate_spec(steps, [], start_url=quiet_origin + "/",
                             name="navz")
        assert "NetRequestWatcher" in spec, (
            "spec 没有 count=0 观察者 —— 断言被静默丢弃：\n" + spec)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=quiet_origin + "/")
            page = ctx.new_page()
            page.goto(quiet_origin + "/")
            test_fn = _run_spec(spec, page, "navz.py")
            test_fn(page)                   # 不抛 = 通过
            browser.close()

    def test_both_chains_agree_positive_and_negative(self, api_origin,
                                                     quiet_origin):
        """T3 双链同判：正向（reload 不发请求）双链通过；反向（观察窗动作
        #go 的迟到 POST）双链都必须失败且指回断言 rc-1。"""
        from generate_spec import generate_spec
        from generate_trace import generate_trace
        from replay_trace import replay_trace
        steps = _steps(quiet_origin)
        spec = generate_spec(steps, [], start_url=quiet_origin + "/",
                             name="navz")
        trace = generate_trace(steps, [], name="navz",
                               start_url=quiet_origin + "/")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=quiet_origin + "/")
            page = ctx.new_page()
            test_fn = _run_spec(spec, page, "navz.py")
            test_fn(page)
            page2 = ctx.new_page()
            page2.goto(quiet_origin + "/")
            exec2 = replay_trace(page2, trace, navigate=False, timeout_ms=8000)
            assert exec2["status"] == "success", exec2["steps"]
            ctx.close()

            # 反向：换 fire 环境 —— reload 的新文档自己发 POST，落进观察窗。
            # 注：观察窗起点在 reload 之前，reload 前后两个文档的 POST 都在
            # 窗内（窗的语义就是「因果动作前后都不该有」）；负断言必须抓住。
            bad_steps = _steps(api_origin)
            bad_spec = generate_spec(bad_steps, [], start_url=api_origin + "/",
                                     name="navz")
            bad_trace = generate_trace(bad_steps, [], name="navz",
                                       start_url=api_origin + "/")
            ctxb = browser.new_context(base_url=api_origin + "/")
            pageb = ctxb.new_page()
            pageb.goto(api_origin + "/")
            bad_fn = _run_spec(bad_spec, pageb, "bad.py")
            with pytest.raises(Exception) as ei:
                bad_fn(pageb)
            assert "请求数" in str(ei.value) or "request_count" in str(ei.value), (
                f"失败形态不对: {type(ei.value).__name__}: {str(ei.value)[:200]}")
            pageb2 = ctxb.new_page()
            pageb2.goto(api_origin + "/")
            execb = replay_trace(pageb2, bad_trace, navigate=False,
                                 timeout_ms=8000)
            assert execb["status"] != "success", execb["steps"]
            fails = [s for s in execb["steps"] if s.get("status") == "failed"]
            assert fails, execb["steps"]
            ctxb.close()
            browser.close()
