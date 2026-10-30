"""页面关闭（tab/popup close）录制缺失失败测试。

场景（quality-log 矩阵「页面关闭」缺口 —— 连续两轮最高风险）：

用户录制中途把 popup 标签页关掉（浏览器级关闭：Ctrl+W / page.close()，
页面上没有可点的关闭按钮），随后继续在 opener 页面上操作。当前录制器：

1. 关闭事件零步骤产出 —— recording.json 完全不可见（injector 随页面销毁，
   没有 window close 兜底上报；record.py 的 on_page_closed 只翻结束位）；
2. 泵循环在「等着的页面被关、opener 还活着」时 break（record.py:
   current[0].wait_for_timeout 抛 TargetClosedError → break）—— 关闭之后
   opener 上的操作全丢，录制提前收尾；
3. 生成/回放两端都没有关页语义：pytest 链不关 popup，trace 链不关 popup，
   回放结束时上下文比录制时多一个页面。page_count 类断言（两条链共用
   expect_page_count）在录制态=1、回放态=2，正反判全错位。

验收基线（任务门禁）：真实录制 + pytest / trace 双回放 + 正反验证。本文件
先锁三件事，全部先失败：

* T1 录制侧：popup 打开 → page.close() 关 popup → opener 后续操作必须
  全部录到，且 recording.json 里有一条「页面被关」的事件步骤；
* T2 生成侧：该关闭步骤必须编译进 pytest spec（拒绝静默丢弃）并进入 trace
  （动作语义是关页，不是 RecordedAction）；
* T3 回放侧：trace 节点被 replay_trace 如实执行 —— popup 真的被关掉，
  执行后 context 页面数与录制终态一致，page_count 断言双链同判。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from playwright.sync_api import sync_playwright

from chrome_path import resolve_chrome


# ── 本地 fixture：opener + popup 页 ────────────────────────────────────────

def _start_server():
    OPENER = (
        b"<!doctype html><html><head><title>close-home</title></head><body>"
        b"<a id='open' href='/popup' target='_blank'>open popup</a>"
        b"<input id='q' placeholder='keyword'>"
        b"<div id='marker'>opener</div>"
        b"</body></html>"
    )
    POPUP = (
        b"<!doctype html><html><head><title>popup</title></head><body>"
        b"<input id='r' placeholder='popup-input'>"
        b"<div id='marker'>popup</div>"
        b"</body></html>"
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = POPUP if self.path.startswith("/popup") else OPENER
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(scope="module")
def close_origin():
    srv = _start_server()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()


# ── T1 录制侧：popup 关闭必须产出步骤且 opener 后续步骤不丢 ────────────────

def _record_close(close_origin, tmp_path, driver):
    from record import record_session

    return record_session(
        start_url=close_origin + "/", name="popup-close",
        out_dir=str(tmp_path), headless=True, on_ready=driver,
    )


def test_popup_close_recorded_and_later_steps_survive(close_origin, tmp_path):
    """点开 popup → page.close() 关掉它 → opener 填值。

    现状（缺陷）：close 无步骤产出（缺陷 1）；且泵在关闭瞬间 break，opener
    上的 fill 根本录不到（缺陷 2）—— 两个都要满足才算过。
    """
    def drive(page, pump):
        pump()
        with page.context.expect_page() as popup_info:
            page.click("#open")
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded")
        pump()
        # 浏览器级关闭标签页（等价用户 Ctrl+W）：页面上没有关闭按钮可点。
        popup.close()
        pump()
        # 关闭后继续在 opener 上操作 —— 这一步必须被录到（缺陷 2 的核心）。
        page.click("#q")
        page.fill("#q", "after-close")
        page.click("#marker")
        pump()

    result = _record_close(close_origin, tmp_path, drive)
    steps = result["steps"]
    close_steps = [
        s for s in steps
        if s.get("type") in ("page_close", "close")
        or s.get("navType") == "close"
    ]
    assert close_steps, (
        "popup 关闭未产生任何录制步骤："
        + json.dumps([{"type": s.get("type"), "sel": (s.get("sel") or "")[:40]}
                      for s in steps], ensure_ascii=False)
    )
    # 缺陷 2：关闭之后的步骤不得丢失
    after = [s for s in steps if s.get("type") == "fill" and s.get("value")]
    assert after, (
        "泵循环在 popup 关闭时提前退出，opener 上的后续步骤全部丢失："
        + json.dumps([{"type": s.get("type"), "sel": (s.get("sel") or "")[:40]}
                      for s in steps], ensure_ascii=False)
    )
    assert any(s.get("value") == "after-close" for s in after), after


# ── T2 生成侧：关闭步骤不得被静默丢弃 ─────────────────────────────────────

def _click_open_step(page_id="p1", origin="http://x"):
    return {
        "id": "act-open", "t": 1000, "type": "click",
        "sel": 'locator("#open")', "kind": "id",
        "url": "/", "pageId": page_id, "pageOrigin": origin,
        "ui": {"templates": {"element": "act-open.png"},
               "pageRect": {"x": 0, "y": 0, "width": 80, "height": 20},
               "pageViewport": {"width": 1280, "height": 720},
               "deviceScaleFactor": 1},
    }


def _fill_popup_step(page_id="p2", opener="p1", origin="http://x"):
    return {
        "id": "act-popup-fill", "t": 1500, "type": "fill",
        "sel": 'locator("#r")', "kind": "id", "value": "popup-input",
        "url": "/popup", "pageId": page_id, "openerPageId": opener,
        "pageOrigin": origin,
    }


def _close_step(step_id="close-1", page_id="p2", opener="p1",
                origin="http://x", t=2000):
    return {
        "id": step_id, "t": t, "type": "page_close",
        "url": "/popup", "pageId": page_id, "openerPageId": opener,
        "pageOrigin": origin,
    }


def _fill_after_step(page_id="p1", origin="http://x", t=2500):
    return {
        "id": "act-after", "t": t, "type": "fill",
        "sel": 'locator("#q")', "kind": "id", "value": "after-close",
        "url": "/", "pageId": page_id, "pageOrigin": origin,
    }


def test_generate_spec_transcribes_popup_close():
    """带 page_close 的录制必须编译出关闭 popup 页面的代码。

    现状：type=page_close 不在 generate_spec 的任何分支里 →
    「未知动作类型」拒发（比静默丢好，但能力缺失：一次无害的关标签页
    会让整份真实录制被拒）。验收要求能转译。
    """
    from generate_spec import generate_spec

    steps = [_click_open_step(), _fill_popup_step(), _close_step(),
             _fill_after_step()]
    spec = generate_spec(steps, [], start_url="http://x/", name="close")
    assert re_close_close_call(spec), (
        "page_close 步骤未被转译进 pytest spec\n" + spec)


def re_close_close_call(spec: str) -> bool:
    """生成链必须真的执行关闭（page.close()），不能只是注释。"""
    import re
    for line in spec.splitlines():
        code = line.strip()
        if code.startswith("#"):
            continue
        if re.search(r"\.close\(\)", code):
            return True
    return False


def test_generate_trace_carries_close_semantics():
    """trace 必须携带关页语义：动作类型落进回放链受支持集合。

    现状：未知类型 step → RecordedAction（回放链不认识 → 拒绝整份 trace）
    或 spec 侧拒发 —— 两条链都拿不到一次真的关闭。
    """
    from generate_trace import generate_trace

    steps = [_click_open_step(), _fill_popup_step(), _close_step(),
             _fill_after_step()]
    trace = generate_trace(steps, [], name="close", start_url="http://x/")
    actions = [
        (nid, node["action"]["type"])
        for nid, node in trace.items()
        if isinstance(node, dict) and nid != "$meta"
    ]
    close_nodes = [(nid, a) for nid, a in actions
                   if a in ("ClosePage", "Close")]
    assert close_nodes, (
        f"trace 未携带关页语义动作；实际动作：{actions}")


# ── T3 回放侧：popup 真的被关掉，页面数与录制终态一致 ──────────────────────

def test_replay_closes_popup_and_page_count_agrees(close_origin):
    """双链同判：回放结束后 context 页面数必须回到 opener-only 的 1。

    现状（缺陷 3）：两条链都不关 popup → 回放终态 2 个页面，与录制终态
    （1 个）不一致 —— page_count 类断言在双链上会得出与录制相反的结论。
    """
    from generate_spec import generate_spec
    from generate_trace import generate_trace
    from replay_trace import replay_trace

    origin = close_origin
    steps = [_click_open_step(origin=origin), _fill_popup_step(origin=origin),
             _close_step(origin=origin), _fill_after_step(origin=origin)]
    spec = generate_spec(steps, [], start_url=origin + "/", name="close")
    trace = generate_trace(steps, [], name="close", start_url=origin + "/")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, executable_path=resolve_chrome())
        ctx = browser.new_context(base_url=origin + "/")
        page = ctx.new_page()
        page.goto(origin + "/")
        # 生成链：开 popup → 填值 → 关 popup → opener 填值，必须通过
        ns = {"__file__": str(__file__)}
        exec(compile(spec, "close.py", "exec"), ns)
        test = next(v for k, v in ns.items()
                    if k.startswith("test_") and callable(v))
        test(page)
        # 录制终态：popup 已关，只剩 opener
        assert len(ctx.pages) == 1, (
            f"生成链回放后 context 有 {len(ctx.pages)} 个页面 —— popup 没被关掉")
        assert page.locator("#q").input_value() == "after-close"
        # 回放链：同一条轨迹必须同样成功，页面数同判
        execution = replay_trace(page, trace, navigate=False, timeout_ms=5000)
        assert execution["status"] == "success", execution["steps"]
        close_nodes = [s for s in execution["steps"]
                       if "Close" in str(s.get("actualAction"))]
        assert close_nodes, execution["steps"]
        assert len(ctx.pages) == 1, (
            f"回放链结束后 context 有 {len(ctx.pages)} 个页面 —— "
            "与录制终态（1 个）不一致，page_count 断言双链判不同")
        browser.close()
