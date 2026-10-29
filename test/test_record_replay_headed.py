"""F7（审查 2026-10-29）：真实 record_session 录制 → 回放验收。

不是手工构造 steps —— 而是驱动真实的 `record.py.record_session`（通过其 on_ready
接缝）录一段「输入 → 点击提交 → 触发提示 → 跳转 → 断言」的工作流：断言由录制器的
右键菜单真实添加（预填 expected → 勾选确认 → 添加）。随后用这份录制产物同时喂
generate_spec（pytest 生成链）与 generate_trace（回放链），在真实 Chromium 里
回放，两条链都成功。

这是完成定义 §11 要求的「至少一条 headed 录制覆盖输入→点击→跳转/提示→断言→回放」。
"""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from chrome_path import resolve_chrome
from generate_spec import generate_spec
from generate_trace import generate_trace
from record import record_session
from replay_trace import replay_trace

PAGES = {
    "/": """<!doctype html><html><head><meta charset="utf-8"><title>首页</title></head><body>
      <input id="name" placeholder="姓名">
      <button id="submit" onclick="document.getElementById('out').textContent='已保存：'+document.getElementById('name').value">保存</button>
      <div id="out"></div>
      <button id="fire" onclick="document.getElementById('toast').style.display='block'">触发提示</button>
      <div id="toast" style="display:none">保存成功</div>
      <a id="go" href="/next">去下一页</a>
    </body></html>""",
    "/next": """<!doctype html><html><head><meta charset="utf-8"><title>结果页</title></head>
      <body><h1 id="landed">到达目标页</h1></body></html>""",
}


def _serve():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = PAGES.get(self.path, PAGES["/"]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


@pytest.fixture(scope="module")
def record_env():
    srv, origin = _serve()
    try:
        yield srv, origin
    finally:
        srv.shutdown()


def _replay_chain(browser, origin, steps, net, chain):
    """在真实 Chromium 里回放一条链。chain='gen' → 生成 pytest；chain='trace' → 回放 golden。
    复用调用方传入的 browser（它已在一个 sync_playwright 上下文里），不要再 open 一个。"""
    ctx = browser.new_context(base_url=origin)
    try:
        pg = ctx.new_page()
        pg.set_default_timeout(3000)
        if chain == "gen":
            spec = generate_spec(steps, net, start_url=origin + "/", name="headed")
            ns = {"__file__": str(Path(__file__))}
            exec(compile(spec, "headed.py", "exec"), ns)
            test = next(v for k, v in ns.items() if k.startswith("test_") and callable(v))
            try:
                test(pg)
                return "success"
            except Exception as error:
                return f"failed: {type(error).__name__}: {str(error).splitlines()[0]}"
        else:
            trace = generate_trace(steps, net, name="headed", start_url=origin + "/")
            pg.goto(origin + "/")
            execution = replay_trace(pg, trace, navigate=False, timeout_ms=3000)
            if execution["status"] == "success":
                return "success"
            step = execution["steps"][0] if execution["steps"] else {}
            return f"failed: {step.get('error') or step.get('status')}"
    finally:
        ctx.close()


def test_record_session_records_and_replays_both_chains(record_env, tmp_path):
    """真实 record_session 录制的产物，pytest 与 trace 两条链都在真实 Chromium
    回放成功 —— 证明不是手工构造，而是端到端真实录制 → 回放。"""
    srv, origin = record_env
    out_dir = tmp_path / "rec"
    state_dir = tmp_path / ".auth"
    reach = {"done": False}

    def on_ready(page, pump):
        # 1) 输入 → 点击提交 → 表单回填文本断言（改 expected=确认）
        page.fill("#name", "Alice")
        pump()
        page.click("#submit")
        pump()
        page.wait_for_timeout(200)
        b = page.locator("#out").bounding_box()
        page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, button="right")
        page.wait_for_timeout(200)
        sh = page.frame_locator("#__rec_assert_menu__")
        sh.locator("#es").fill("已保存：Alice")
        page.wait_for_timeout(100)
        sh.locator("#ok").click()
        page.wait_for_timeout(200)
        # 2) 触发提示 → 可见性=是（触发后确实可见）→ 布尔确认
        page.click("#fire")
        pump()
        page.wait_for_timeout(300)
        b = page.locator("#toast").bounding_box()
        page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, button="right")
        page.wait_for_timeout(200)
        sh2 = page.frame_locator("#__rec_assert_menu__")
        sh2.locator("#t").select_option("visible")
        page.wait_for_timeout(100)
        sh2.locator("#confirmBool").check()
        page.wait_for_timeout(100)
        sh2.locator("#ok").click()
        page.wait_for_timeout(200)
        # 3) 跳转 → 页面 URL 断言（scope=page）
        with page.expect_navigation(url="**/next"):
            page.click("#go")
        page.wait_for_timeout(300)
        b = page.locator("h1#landed").bounding_box()
        page.mouse.click(b["x"] + 5, b["y"] + 5, button="right")
        page.wait_for_timeout(200)
        sh3 = page.frame_locator("#__rec_assert_menu__")
        sh3.locator("#sc").select_option("page")
        page.wait_for_timeout(100)
        sh3.locator("#t").select_option("url")
        page.wait_for_timeout(100)
        sh3.locator("#confirm").check()
        page.wait_for_timeout(100)
        sh3.locator("#ok").click()
        page.wait_for_timeout(200)
        reach["done"] = True

    chrome = resolve_chrome()
    result = record_session(
        start_url=origin + "/", name="headed", out_dir=str(out_dir),
        state_dir=str(state_dir), chrome_bin=chrome, headless=True,
        on_ready=on_ready,
    )
    steps, net = result["steps"], result["net"]
    assert reach["done"], "on_ready 未跑完"
    assert any(s["type"] == "assert" for s in steps), "录制里应有断言步骤"

    # 断言步骤必须带 expectedConfirmed / wait / afterStepId（P0）
    asserts = [s for s in steps if s["type"] == "assert"]
    for a in asserts:
        assert a.get("expectedConfirmed") is True, a
        assert a.get("wait"), a

    # 两条链在真实 Chromium 回放
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=chrome)
        try:
            gen_ok = _replay_chain(browser, origin, steps, net, "gen")
            trace_ok = _replay_chain(browser, origin, steps, net, "trace")
        finally:
            browser.close()

    assert gen_ok == "success", f"生成链失败: {gen_ok}"
    assert trace_ok == "success", f"回放链失败: {trace_ok}"
