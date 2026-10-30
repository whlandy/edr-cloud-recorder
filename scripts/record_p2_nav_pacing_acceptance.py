"""真实 headed 录制 + 双回放 + 正反验证：回放节奏 / 慢导航（P2.10）。

场景（quality-log 矩阵「回放节奏 / 导航等待」整行空白，2026-10-31 本轮）：

  1. 真实 headed Chrome 录制：填输入框 → 点按钮（setTimeout 300ms 后跳
     /slow-next，服务器**延迟 7s** 才响应）→ 等 ~9s 导航完成 → 在
     /slow-next 上填值并对输入框下 value 断言。
  2. record.py 生成管线原子落盘 recording.json / trace.json / test_*.py。
  3. 生成链（pytest 函数真实执行）：wait_for_recorded_url 以 20s 导航预算
     等到慢导航完成 → 正向通过。
  4. 回放链（replay_trace）：_page_for_node 的路线等待必须用同样的导航
     预算（NAV_ROUTE_WAIT_MS=20s）→ 正向通过。修复前用单步定位预算 5s，
     在同一录制上 trace 链报「页面路径不符」—— 双链分歧（test_p2_nav_pacing
     先红证明）。
  5. 反向：篡改 value 断言 expected（world → tampered），两条链都必须
     明确失败。
  6. 反向：禁用修复（NAV_ROUTE_WAIT_MS → 0，等价回单步预算）重放 trace
     链，必须以「路径不符」失败 —— 证明录制产物确实暴露了被修复的缺陷
     （录制↔回放节奏差是真实的，不是测试构造的）。

产物在 recordings/p2-nav-pacing-recording/ 下；recordings/ 按仓库策略
gitignore，由本脚本复现。
"""

import json
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))

from playwright.sync_api import sync_playwright  # noqa: E402

from chrome_path import resolve_chrome  # noqa: E402
from replay_trace import replay_trace  # noqa: E402
from record import record_session  # noqa: E402

CASE = "p2-nav-pacing-recording"
OUT = Path(__file__).resolve().parents[1] / "recordings" / CASE

SLOW_DELAY = 7.0

PAGE_MAIN = """<!doctype html><html><head><meta charset="utf-8">
<title>pacing-main</title></head><body>
<input id="q" placeholder="keyword">
<button id="go" onclick="setTimeout(function(){{location.href='/slow-next';}},300)">slow go</button>
<div id="marker">page-main</div>
</body></html>"""

PAGE_SLOW_NEXT = """<!doctype html><html><head><meta charset="utf-8">
<title>slow-next</title></head><body>
<input id="q" placeholder="keyword">
<div id="marker">slow-next</div>
</body></html>"""


def _serve():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/slow-next"):
                time.sleep(SLOW_DELAY)
            body = (PAGE_SLOW_NEXT if self.path.startswith("/slow-next")
                    else PAGE_MAIN).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def drive(page, pump):
    """on_ready 回调：真实 headed 页面上操作一遍慢导航流。

    节奏与真人一致：填值 → 点别处让 change 落地 → 点慢按钮（点击本身
    不导航，300ms 后 setTimeout 才跳，服务器再拖 7s）→ 等导航完成 →
    在新页面上填值 → 右键下 value 断言。
    """
    page.bring_to_front()
    pump()
    # 1) 首页填值
    page.click("#q")
    page.fill("#q", "hello")
    page.click("#marker")
    page.wait_for_timeout(300)
    pump()
    # 2) 点慢按钮：导航在 ~7.3s 后才 commit。此刻落盘的步骤在 / 上。
    page.click("#go")
    # 等慢导航真正完成（轮询 URL，最长 15s）
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if page.url.endswith("/slow-next"):
            break
        page.wait_for_timeout(200)
    assert page.url.endswith("/slow-next"), (
        f"慢导航未在预算内完成：{page.url}")
    pump()
    # 3) 在 /slow-next 上填值（导航后状态），等 change 落地
    page.click("#q")
    page.fill("#q", "world")
    page.click("#marker")
    page.wait_for_timeout(300)
    pump()
    # 4) 右键对 #q 下 value 断言（expected=world，显式确认）
    box = page.locator("#q").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2,
                     box["y"] + box["height"] / 2, button="right")
    menu = page.frame_locator("#__rec_assert_menu__")
    menu.locator("#t").wait_for(state="visible")
    menu.locator("#t").select_option("value")
    menu.locator("#op").select_option("equals")
    menu.locator("#es").fill("world")
    # P0 红线：value 断言要求显式确认 expected
    menu.locator("#confirm").check()
    menu.get_by_role("button", name="添加断言").click()
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
        out_dir=str(OUT.parent), state_dir="/tmp/rec-p2-pacing-state",
        on_ready=drive,
    )
    print("record ok:", result["raw_file"])

    recording = json.loads(Path(result["raw_file"]).read_text())
    steps = recording["steps"]
    print("recorded steps:",
          [(s.get("type"), (s.get("sel") or s.get("assertion") or ""),
            s.get("url")) for s in steps])
    # 录制核验：fill(hello) 在 / 上，慢导航点击也在 / 上，导航后的
    # fill(world) + value 断言必须落在 /slow-next 的 pageUrl 名下。
    fills = {s.get("value"): s for s in steps if s.get("type") == "fill"}
    assert "hello" in fills and "world" in fills, (
        f"录制步骤缺 fill：{[(s.get('type'), s.get('value')) for s in steps]}")
    assert fills["hello"].get("url") == "/", fills["hello"]
    assert fills["world"].get("url") == "/slow-next", fills["world"]
    value_asserts = [s for s in steps if s.get("type") == "assert"
                     and s.get("assertion") == "value"]
    assert value_asserts, "value 断言未被录制"
    va = value_asserts[0]
    assert va.get("expected") == "world" and va.get("expectedConfirmed") is True, va
    assert va.get("url") == "/slow-next", va
    print("recording artifact checks: PASS (slow-nav causality + confirmed expected)")

    trace = json.loads(Path(result["trace_file"]).read_text())
    meta = trace["$meta"]["attach"]
    assert meta.get("status") == "ready", meta.get("status")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
                                     executable_path=resolve_chrome())
        ctx = browser.new_context(base_url=origin + "/")

        # ── 生成链正向：wait_for_recorded_url 必须等到慢导航 ──
        page = ctx.new_page()
        _run_spec(Path(result["spec_file"]).read_text(), page, f"{CASE}.py")
        assert page.url.endswith("/slow-next"), page.url
        assert page.locator("#q").input_value() == "world", (
            "生成链：/slow-next 上的 fill 未生效 —— 慢导航未被等待")
        print("pytest chain positive: PASS (slow nav waited, fill survived)")

        # ── 回放链正向：NAV_ROUTE_WAIT_MS 导航预算 ──
        page2 = ctx.new_page()
        page2.goto(origin + "/")
        exec2 = replay_trace(page2, trace, navigate=False, timeout_ms=5_000)
        assert exec2["status"] == "success", exec2["steps"]
        assert page2.url.endswith("/slow-next"), page2.url
        assert page2.locator("#q").input_value() == "world"
        print("trace chain positive: PASS (slow nav waited, fill survived)")

        # ── 反向 1：篡改 value expected（world → tampered）→ 双链都失败 ──
        steps2 = json.loads(json.dumps(steps))
        for s in steps2:
            if s.get("type") == "assert" and s.get("assertion") == "value":
                s["expected"] = "tampered"
        from generate_spec import generate_spec  # noqa: E402
        from generate_trace import generate_trace  # noqa: E402
        spec_bad = generate_spec(steps2, recording["net"],
                                 start_url=origin + "/", name=CASE)
        trace_bad = generate_trace(steps2, recording["net"], name=CASE,
                                   start_url=origin + "/")
        page3 = ctx.new_page()
        try:
            _run_spec(spec_bad, page3, "bad.py")
            raise SystemExit("反向验证失败：篡改 expected 后生成链居然通过")
        except AssertionError as e:
            print(f"pytest chain tampered: FAIL as expected → {str(e)[:140]}")
        page4 = ctx.new_page()
        page4.goto(origin + "/")
        exec3 = replay_trace(page4, trace_bad, navigate=False,
                             timeout_ms=5_000)
        assert exec3["status"] != "success", "篡改后 trace 链居然通过"
        print("trace chain tampered: FAIL as expected")

        # ── 反向 2：禁用修复重放 —— trace 链必须以「路径不符」失败 ──
        # 证明真实录制产物确实暴露了被修复的缺陷（导航等待预算差）。
        import replay_trace as rt  # noqa: E402
        saved = rt.NAV_ROUTE_WAIT_MS
        try:
            rt.NAV_ROUTE_WAIT_MS = 0
            page5 = ctx.new_page()
            page5.goto(origin + "/")
            exec4 = replay_trace(page5, trace, navigate=False,
                                 timeout_ms=5_000)
            assert exec4["status"] != "success", (
                "禁用修复后 trace 链居然通过 —— 录制产物没有暴露节奏差缺陷？")
            errors = [s.get("error", "") for s in exec4["steps"]
                      if s.get("status") == "failed"]
            assert any("路径不符" in e for e in errors), errors
            print("trace chain fix-disabled: FAIL as expected (路径不符, "
                  "缺陷形态与单测红灯一致)")
        finally:
            rt.NAV_ROUTE_WAIT_MS = saved

        browser.close()
    print("ALL GATES PASS")


if __name__ == "__main__":
    main()
