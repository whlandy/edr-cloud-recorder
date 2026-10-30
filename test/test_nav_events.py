"""页面导航事件录制缺失（reload / 前进后退）失败测试。

场景（quality-log 矩阵「页面 reload/前进后退/关闭」缺口）：

用户在录制时按 F5（或浏览器后退）。当前录制器对这类导航**零步骤产出**：
keydown 只记 Enter（recorder-inject.mjs:1746），unload 时页面本地 steps
数组随页面一起销毁 —— 重载在 recording.json 里完全不可见。后果：

1. generate_spec / generate_trace 拿不到任何可转译的步骤，回放少做一次
   重载，页面状态与录制时不同；
2. 录制时确认过的断言（如「重载后输入框为空」）在回放里因状态错位而失败，
   失败信息指向断言或元素，而不是真正的原因（丢了导航步骤）。

验收基线（任务门禁）：真实录制 + pytest / trace 双回放 + 正反验证。本文件
先锁三件事，全部先失败：

* T1 录制侧：record_session 驱动「填值 → reload → 断言输入框为空」必须
  产出一条 reload 步骤（页面级导航事件），且 reload 后的断言仍被录制；
* T2 生成侧：该 reload 步骤必须编译进 pytest spec（拒绝静默丢弃）并进入
  trace（动作为 Reload / BackForward 语义，不是 RecordedAction）；
* T3 回放侧：trace 节点被 replay_trace 如实执行 —— 页面真的重载，
  且执行记录标明导航语义。
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from playwright.sync_api import sync_playwright

from chrome_path import resolve_chrome


# ── 本地 fixture：首页可填输入框 + 重载后值归零；/next 供后退起点 ──────────

def _start_server():
    PAGE = (
        b"<!doctype html><html><head><title>nav-fixture</title></head><body>"
        b"<input id='q' placeholder='keyword'>"
        b"<a id='to-next' href='/next'>go next</a>"
        b"<div id='marker'>page-a</div>"
        b"</body></html>"
    )
    NEXT = (
        b"<!doctype html><html><head><title>next</title></head><body>"
        b"<div id='marker'>page-b</div></body></html>"
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = NEXT if self.path.startswith("/next") else PAGE
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(scope="module")
def nav_origin():
    srv = _start_server()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()


# ── T1 录制侧：reload 必须产出导航步骤 ─────────────────────────────────────

def _record_nav(nav_origin, tmp_path, driver):
    from record import record_session

    return record_session(
        start_url=nav_origin + "/", name="nav-reload",
        out_dir=str(tmp_path), headless=True, on_ready=driver,
    )


def test_reload_produces_step_and_later_steps_survive(nav_origin, tmp_path):
    """填值 → F5 → 断言输入框为空：录制必须包含 reload 事件步骤。

    现状（缺陷）：reload 不产生任何步骤 —— 既没有页面级导航事件，
    也没有 unload 前的兜底上报。生成器看不到这次重载。
    """
    def drive(page, pump):
        pump()
        page.fill("#q", "hello")
        pump()
        page.reload(wait_until="domcontentloaded")
        pump()
        # 重载后输入框应为空 —— 用户在录制菜单对它下断言（模拟录制产物
        # 中 assert 步骤的形状：scope=element, value, expected=''）。
        page.evaluate("() => window.__rec && window.__rec.drain()")
        steps_before = page.evaluate(
            "() => (window.__rec ? window.__rec.steps.length : -1)")
        assert steps_before == 0, steps_before

    result = _record_nav(nav_origin, tmp_path, drive)
    steps = result["steps"]
    # 缺陷断言：必须存在一条「页面被重载」的事件步骤
    nav_steps = [s for s in steps if s.get("type") in ("reload", "navigate")
                 or s.get("assertion") == "reloaded"
                 or (s.get("scope") == "page" and s.get("navType") == "reload")]
    assert nav_steps, (
        "reload 未产生任何录制步骤："
        + json.dumps([{ "type": s.get("type"), "label": s.get("label"),
                        "scope": s.get("scope")} for s in steps],
                     ensure_ascii=False)
    )
    # 该步骤必须标记导航类型，且回放端能区分 reload 与首次加载
    assert any(s.get("navType") == "reload" for s in nav_steps), nav_steps


def test_back_forward_produces_step(nav_origin, tmp_path):
    """首页 → /next → 后退：录制必须包含 back_forward 导航事件步骤。"""
    def drive(page, pump):
        pump()
        page.click("#to-next")
        page.wait_for_load_state("domcontentloaded")
        pump()
        page.go_back(wait_until="domcontentloaded")
        pump()

    result = _record_nav(nav_origin, tmp_path, drive)
    steps = result["steps"]
    nav_steps = [s for s in steps if s.get("navType") in ("back_forward", "reload")]
    assert nav_steps, (
        "后退未产生任何导航事件步骤："
        + json.dumps([{"type": s.get("type"), "url": s.get("url")}
                      for s in steps], ensure_ascii=False)
    )
    assert any(s.get("navType") == "back_forward" for s in nav_steps)


# ── T2 生成侧：reload 步骤不得被静默丢弃 ───────────────────────────────────

def _reload_step(step_id="nav-1", origin="http://x"):
    return {
        "id": step_id, "t": 1500, "type": "navigate", "navType": "reload",
        "url": "/", "pageId": "p1", "pageOrigin": origin,
    }


def _fill_step(origin="http://x"):
    return {
        "id": "act-1", "t": 1000, "type": "fill",
        "sel": 'locator("#q")', "kind": "id", "value": "hello",
        "url": "/", "pageId": "p1", "pageOrigin": origin,
    }


def _empty_value_assert(origin="http://x"):
    return {
        "id": "assert-1", "t": 2000, "type": "assert",
        "sel": 'locator("#q")', "kind": "id",
        "assertion": "value", "expected": "", "op": "equals",
        "expectedConfirmed": True,
        "url": "/", "pageId": "p1", "pageOrigin": origin,
    }


def test_generate_spec_transcribes_reload_step():
    """带 reload 的录制必须编译出 page.reload()（或等价导航）。

    现状：type=navigate 不在 generate_spec 的任何分支里 →
    「未知动作类型 navigate」拒发（比静默丢好，但能力仍是缺失：
    真实录制产物会因为一次无害的重载整份被拒）。验收要求能转译。
    """
    from generate_spec import generate_spec

    steps = [_fill_step(), _reload_step(), _empty_value_assert()]
    spec = generate_spec(steps, [], start_url="http://x/", name="nav")
    assert re.search(r"reload\(\)", spec), (
        "reload 步骤未被转译进 pytest spec\n" + spec)


def test_generate_trace_carries_reload_semantics():
    """trace 必须携带导航语义：动作类型落进受支持集合。

    现状：未知类型 step → RecordedAction（回放链不支持）或
    spec 侧拒发 —— 两条链都拿不到一次真的 reload。
    """
    from generate_trace import generate_trace

    steps = [_fill_step(), _reload_step(), _empty_value_assert()]
    trace = generate_trace(steps, [], name="nav", start_url="http://x/")
    import scripts  # noqa: F401  占位防误删
    actions = [
        (nid, node["action"]["type"])
        for nid, node in trace.items()
        if isinstance(node, dict) and nid != "$meta"
    ]
    # 找 reload 节点：动作品类必须是回放链认识的（不能是 RecordedAction）
    reload_nodes = [
        (nid, a) for nid, a in actions
        if a in ("Reload", "Navigate", "GoBack", "GoForward")
    ]
    assert reload_nodes, (
        f"trace 未携带 reload 语义动作；实际动作：{actions}")


# ── T3 回放侧：reload 节点被如实执行 ───────────────────────────────────────

def test_replay_executes_reload_between_steps(nav_origin):
    """双链同判：带 reload 的轨迹回放后输入框必须真的是空的。"""
    from generate_spec import generate_spec
    from generate_trace import generate_trace
    from replay_trace import replay_trace

    origin = nav_origin
    steps = [
        {**_fill_step(origin), "sel": 'locator("#q")'},
        {**_reload_step(origin=origin)},
        {**_empty_value_assert(origin),
         "sel": 'locator("#q")', "kind": "id"},
    ]
    spec = generate_spec(steps, [], start_url=origin + "/", name="nav")
    trace = generate_trace(steps, [], name="nav", start_url=origin + "/")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, executable_path=resolve_chrome())
        ctx = browser.new_context(base_url=origin + "/")
        page = ctx.new_page()
        page.goto(origin + "/")
        # 生成链：填 hello → reload → 断言为空，必须通过
        ns = {"__file__": str(__file__)}
        exec(compile(spec, "nav.py", "exec"), ns)
        test = next(v for k, v in ns.items()
                    if k.startswith("test_") and callable(v))
        test(page)
        # 回放链：同一条轨迹必须同样成功（状态一致：输入框为空）
        execution = replay_trace(page, trace, navigate=False, timeout_ms=3000)
        assert execution["status"] == "success", execution["steps"]
        assert page.locator("#q").input_value() == "", (
            "回放后输入框不为空 —— reload 没有真的执行")
        browser.close()


def test_replay_navigate_skips_pre_route_assert(nav_origin):
    """back_forward 节点在路线回到录制值**之前**执行 —— 路线断言必须让路。

    缺陷形态（真实录制闭环 2026-10-30 暴露）：页面先跳到 /next 再后退，
    Navigate/back_forward 节点执行前的路线是 /next，而节点 provenance 记的
    是 /。预检 _assert_node_page 若不放行，会把这次后退报成
    「页面路径不符」—— 真正该做的导航被路线断言吞掉。
    """
    from generate_trace import generate_trace
    from replay_trace import replay_trace

    origin = nav_origin
    steps = [
        {
            "id": "act-link", "t": 1000, "type": "click",
            "sel": 'locator("#to-next")', "kind": "id",
            "url": "/", "pageId": "p1", "pageOrigin": origin,
            "ui": {"templates": {"element": "act-link.png"},
                   "pageRect": {"x": 0, "y": 0, "width": 60, "height": 20},
                   "pageViewport": {"width": 1280, "height": 720},
                   "deviceScaleFactor": 1},
        },
        {
            "id": "nav-back", "t": 2000, "type": "navigate",
            "navType": "back_forward",
            "url": "/", "pageId": "p1", "pageOrigin": origin,
        },
    ]
    trace = generate_trace(steps, [], name="nav", start_url=origin + "/")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, executable_path=resolve_chrome())
        ctx = browser.new_context(base_url=origin + "/")
        page = ctx.new_page()
        page.goto(origin + "/")
        execution = replay_trace(page, trace, navigate=False, timeout_ms=5000)
        back = execution["steps"][-1]
        assert back["status"] == "success", (
            f"back_forward 节点被路线断言拦截: {back.get('error')}")
        assert execution["status"] == "success", execution["steps"]
        assert "/next" not in page.url, (
            "后退没有真的执行 —— 页面仍停留在 /next")
        browser.close()
