"""真实 headed 录制 + 双回放 + 正反验证：页面导航事件（reload / 前进后退）。

场景（quality-log 矩阵「页面 reload/前进后退/关闭」缺口，2026-10-30 本轮）：

  1. 真实 headed Chrome 录制：填输入框 → page.reload() →（重载后输入框为空）
     → 点链接跳 /next → page.go_back() →（回到首页）
  2. record.py 生成管线原子落盘 recording.json / trace.json / test_*.py
  3. 生成链（pytest 函数真实执行）：包含 page.reload() / page.go_back()，
     正向通过
  4. 回放链（replay_trace）：Navigate 节点如实执行，正向通过
  5. 反向：篡改重载后断言的 expected（'' → 'hello'，即假装重载不清空输入），
     两条链都必须明确失败
  6. 反向：从 steps 里删掉 navigate 步骤再生成 —— 回放后输入框必须非空
     （状态与录制时不一致被还原出来），证明该步骤承载真实因果

产物在 recordings/p2-nav-events-recording/ 下，与本提交绑定。
"""

import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))

from playwright.sync_api import sync_playwright  # noqa: E402

from chrome_path import resolve_chrome  # noqa: E402
from generate_spec import generate_spec  # noqa: E402
from generate_trace import generate_trace  # noqa: E402
from replay_trace import replay_trace  # noqa: E402
from record import record_session  # noqa: E402

CASE = "p2-nav-events-recording"
OUT = Path(__file__).resolve().parents[1] / "recordings" / CASE

PAGE_A = """<!doctype html><html><head><meta charset="utf-8">
<title>nav-home</title></head><body>
<input id="q" placeholder="keyword">
<a id="to-next" href="/next">go next</a>
<div id="marker">page-a</div>
</body></html>"""

PAGE_B = """<!doctype html><html><head><meta charset="utf-8">
<title>next</title></head><body>
<div id="marker">page-b</div>
<a id="to-home" href="/">back home</a>
</body></html>"""


def _serve():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = (PAGE_B if self.path.startswith("/next") else PAGE_A)
            body = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def drive(page, pump):
    """on_ready 回调：真实 headed 页面上操作一遍导航流。

    输入按真人节奏：先点输入框聚焦、填值、再点别处让 change 落地
    （Playwright 的 fill() 不发 change，没有 blur 的话录制器收不到
    fill 步骤 —— 这是输入事件语义，不是录制器缺陷）。
    """
    page.bring_to_front()
    pump()
    # 1) 点输入框聚焦，填值，点空白处触发 change → fill 步骤落地
    page.click("#q")
    page.fill("#q", "hello")
    page.click("#marker")
    page.wait_for_timeout(200)
    pump()
    # 2) 重载：输入框清空（页面无持久化），并产出 navigate/reload 步骤
    page.reload(wait_until="load")
    page.wait_for_timeout(400)
    pump()
    # 3) 重载后输入框应为空 —— 用录制菜单对它下断言。expected='' 走不了
    #    静默确认（P0 红线：添加按钮禁用），空值断言的正确形态是 is_blank。
    box = page.locator("#q").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2,
                     box["y"] + box["height"] / 2, button="right")
    menu = page.frame_locator("#__rec_assert_menu__")
    menu.locator("#t").wait_for(state="visible")
    menu.locator("#t").select_option("value")
    menu.locator("#op").select_option("is_blank")
    # P0 红线：string 断言（含 is_blank）要求显式确认 —— 勾「确认使用当前值」
    menu.locator("#confirm").check()
    menu.get_by_role("button", name="添加断言").click()
    page.wait_for_timeout(400)
    pump()
    # 4) 点链接跳 /next，再后退 —— 产出 navigate/back_forward 步骤
    page.click("#to-next")
    page.wait_for_load_state("load")
    page.wait_for_timeout(400)
    pump()
    page.go_back(wait_until="load")
    page.wait_for_timeout(400)
    pump()


def _run_spec(spec_text, page, tag):
    ns = {"__file__": str(OUT / f"test_{CASE}.py")}
    exec(compile(spec_text, tag, "exec"), ns)
    test_fn = next(v for k, v in ns.items()
                   if k.startswith("test_") and callable(v))
    test_fn(page)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    srv, origin = _serve()
    result = record_session(
        start_url=origin + "/", name=CASE,
        out_dir=str(OUT.parent), state_dir="/tmp/rec-p2-nav-state",
        on_ready=drive,
    )
    print("record ok:", result["raw_file"])

    recording = json.loads(Path(result["raw_file"]).read_text())
    steps = recording["steps"]
    nav_steps = [s for s in steps if s.get("type") == "navigate"]
    assert len(nav_steps) >= 2, (
        f"录制必须包含 reload 与 back_forward 两条导航步骤，实际：{steps}")
    kinds = sorted(s["navType"] for s in nav_steps)
    assert kinds == ["back_forward", "reload"], kinds
    print("recorded nav steps:", kinds)
    value_asserts = [s for s in steps if s.get("type") == "assert"
                     and s.get("assertion") == "value"]
    assert value_asserts, "重载后的 value 断言未被录制"

    spec_text = Path(result["spec_file"]).read_text()
    trace = json.loads(Path(result["trace_file"]).read_text())
    meta = trace["$meta"]["attach"]          # $meta 也是节点形态，状态在 attach 里
    assert meta.get("status") == "ready", meta.get("status")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
                                     executable_path=resolve_chrome())
        ctx = browser.new_context(base_url=origin + "/")

        # ── 生成链正向 ──
        page = ctx.new_page()
        _run_spec(spec_text, page, f"{CASE}.py")
        assert page.locator("#q").input_value() == "", "生成链回放后输入框应为空"
        print("pytest chain positive: PASS (incl. page.reload/go_back)")

        # ── 回放链正向 ──
        exec2 = replay_trace(page, trace, navigate=False, timeout_ms=5000)
        assert exec2["status"] == "success", exec2["steps"]
        nav_nodes = [s for s in exec2["steps"]
                     if s.get("actualAction") == "Navigate"]
        assert len(nav_nodes) >= 2, nav_nodes
        assert page.locator("#q").input_value() == "", "回放链后输入框应为空"
        print("trace chain positive: PASS (Navigate executed", len(nav_nodes), "x)")

        # ── 反向 1：篡改 expected（'' → 'hello'，假装重载不清空输入）──
        steps2 = json.loads(json.dumps(steps))
        for s in steps2:
            if s.get("type") == "assert" and s.get("assertion") == "value":
                s["expected"] = "hello"
        spec_bad = generate_spec(steps2, recording["net"],
                                 start_url=origin + "/", name=CASE)
        trace_bad = generate_trace(steps2, recording["net"], name=CASE,
                                   start_url=origin + "/")
        page2 = ctx.new_page()
        try:
            _run_spec(spec_bad, page2, "bad.py")
            raise SystemExit("反向验证失败：篡改 expected 后生成链居然通过")
        except AssertionError as e:
            msg = str(e)
            print(f"pytest chain tampered: FAIL as expected → {msg[:140]}")
        exec3 = replay_trace(page2, trace_bad, navigate=False, timeout_ms=5000)
        assert exec3["status"] != "success", "篡改后 trace 链居然通过"
        print("trace chain tampered: FAIL as expected")

        # ── 反向 2：删掉 navigate 步骤再生成 —— 状态必须错位（输入框非空）──
        # 这证明 navigate 步骤承载真实因果：没有它，回放做出的是另一件事。
        steps3 = [s for s in json.loads(json.dumps(steps))
                  if s.get("type") != "navigate"]
        trace_no_nav = generate_trace(steps3, recording["net"], name=CASE,
                                      start_url=origin + "/")
        page3 = ctx.new_page()
        page3.goto(origin + "/")
        exec4 = replay_trace(page3, trace_no_nav, navigate=False,
                             timeout_ms=5000)
        # fill hello 之后没有 reload：输入框里还是 hello，重载后的「为空」断言
        # 应当失败（或者 fill 直接被覆盖的时序让断言通过但状态错位 —— 任一
        # 形态都必须是「非 success」，不能静默一致）。
        held = page3.locator("#q").input_value()
        assert exec4["status"] != "success" or held == "hello", (
            f"删掉导航步骤后状态竟然与录制一致？status={exec4['status']} "
            f"input={held!r}")
        print(f"trace chain without nav steps: state diverges as expected "
              f"(status={exec4['status']}, input={held!r})")

        browser.close()
    print("ALL GATES PASS")


if __name__ == "__main__":
    main()
