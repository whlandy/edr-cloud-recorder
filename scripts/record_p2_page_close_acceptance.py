"""真实 headed 录制 + 双回放 + 正反验证：页面关闭（tab/popup close）。

场景（quality-log 矩阵「页面关闭」缺口，2026-10-30 本轮）：

  1. 真实 headed Chrome 录制：opener 点链接开 popup（target=_blank）→
     popup 里填值 → 浏览器级关闭 popup（page.close()，等价用户 Ctrl+W ——
     页面上没有可点的关闭按钮，合成关页步骤才无法被「点击本身」替代）→
     opener 填值 → 用录制菜单在 opener 上添加 page_count equals 1 断言
     （录制终态只有 opener 一个页面）
  2. record.py 生成管线原子落盘 recording.json / trace.json / test_*.py
  3. 生成链（pytest 函数真实执行）：popup 被关掉，page_count 断言通过
  4. 回放链（replay_trace）：ClosePage 节点如实执行，页面数同判
  5. 反向 1：篡改 page_count expected（1 → 2，假装 popup 没被关），
     两条链都必须明确失败
  6. 反向 2：从 steps 里删掉 page_close 步骤再生成 —— 回放终态多一个
     标签页（状态与录制不一致被还原出来），证明该步骤承载真实因果

产物在 recordings/p2-page-close-recording/ 下，与本提交绑定。
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

CASE = "p2-page-close-recording"
OUT = Path(__file__).resolve().parents[1] / "recordings" / CASE

OPENER_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>close-home</title></head><body>
<a id="open" href="/popup" target="_blank">open popup</a>
<input id="q" placeholder="keyword">
<div id="marker">opener</div>
</body></html>"""

POPUP_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>popup</title></head><body>
<input id="r" placeholder="popup-input">
<div id="marker">popup</div>
</body></html>"""


def _serve():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = (POPUP_PAGE if self.path.startswith("/popup")
                    else OPENER_PAGE).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def drive(page, pump):
    """on_ready 回调：真实 headed 页面上操作一遍关页流。

    输入按真人节奏：点击聚焦、填值、点空白处让 change 落地
    （Playwright 的 fill() 不发 change —— 见 nav-events 轮注释）。
    """
    page.bring_to_front()
    pump()
    # 1) opener 点链接开 popup（target=_blank → 真实新标签页）
    with page.context.expect_page() as popup_info:
        page.click("#open")
    popup = popup_info.value
    popup.wait_for_load_state("load")
    pump()
    # 2) popup 里填值（真人节奏：点击聚焦 → fill → blur）
    popup.click("#r")
    popup.fill("#r", "popup-input")
    popup.click("#marker")
    page.wait_for_timeout(300)
    pump()
    # 3) 浏览器级关闭 popup（等价用户 Ctrl+W）。页面上没有关闭按钮 ——
    #    这样回放里唯一的「关页」来源就是录制到的 page_close 步骤。
    popup.close()
    page.wait_for_timeout(300)
    pump()
    # 4) opener 填值 —— 关页之后的步骤不得丢（泵循环加固的验收点）
    page.click("#q")
    page.fill("#q", "after-close")
    page.click("#marker")
    page.wait_for_timeout(300)
    pump()
    # 5) 录制菜单添加 page_count equals 1 断言（录制终态只有 opener 一页）
    box = page.locator("#marker").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2,
                     box["y"] + box["height"] / 2, button="right")
    menu = page.frame_locator("#__rec_assert_menu__")
    menu.locator("#t").wait_for(state="visible")
    menu.locator("#sc").select_option("page")
    menu.locator("#t").select_option("page_count")
    menu.locator("#op").select_option("equals")
    menu.locator("#es").fill("1")
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
        out_dir=str(OUT.parent), state_dir="/tmp/rec-p2-close-state",
        on_ready=drive,
    )
    print("record ok:", result["raw_file"])

    recording = json.loads(Path(result["raw_file"]).read_text())
    steps = recording["steps"]
    close_steps = [s for s in steps if s.get("type") == "page_close"]
    assert len(close_steps) == 1, (
        f"录制必须包含一条 page_close 步骤，实际："
        + json.dumps([{"type": s.get("type")} for s in steps], ensure_ascii=False))
    print("recorded steps:",
          [(s.get("type"), (s.get("sel") or s.get("assertion") or "")) for s in steps])
    # 关页之后的 opener 填值必须被录到（泵循环加固）
    after_fills = [s for s in steps if s.get("type") == "fill"
                   and s.get("value") == "after-close"]
    assert after_fills, "关页后 opener 上的步骤丢失（泵循环提前退出）"
    page_counts = [s for s in steps if s.get("type") == "assert"
                   and s.get("scope") == "page"
                   and s.get("assertion") == "page_count"]
    assert page_counts and page_counts[0].get("expected") == 1, page_counts
    assert page_counts[0].get("expectedConfirmed") is True, page_counts[0]

    spec_text = Path(result["spec_file"]).read_text()
    trace = json.loads(Path(result["trace_file"]).read_text())
    meta = trace["$meta"]["attach"]
    assert meta.get("status") == "ready", meta.get("status")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
                                     executable_path=resolve_chrome())
        ctx = browser.new_context(base_url=origin + "/")

        # ── 生成链正向 ──
        page = ctx.new_page()
        _run_spec(spec_text, page, f"{CASE}.py")
        assert len(ctx.pages) == 1, (
            f"生成链回放后 context 有 {len(ctx.pages)} 个页面 —— popup 没被关掉")
        assert page.locator("#q").input_value() == "after-close"
        print("pytest chain positive: PASS (popup closed, page_count=1)")

        # ── 回放链正向 ──
        exec2 = replay_trace(page, trace, navigate=False, timeout_ms=5000)
        assert exec2["status"] == "success", exec2["steps"]
        close_nodes = [s for s in exec2["steps"]
                       if s.get("actualAction") == "ClosePage"]
        assert len(close_nodes) == 1, exec2["steps"]
        assert len(ctx.pages) == 1, (
            f"回放链结束后 context 有 {len(ctx.pages)} 个页面")
        print("trace chain positive: PASS (ClosePage executed, pages=1)")

        # ── 反向 1：篡改 page_count expected（1 → 2，假装 popup 没被关）──
        # 注意必须用**全新 context**：page_count 断言数的是整个 context 的页面，
        # 复用正向链的 context 会把正向残留的页面数进去，反向就被污染放行。
        steps2 = json.loads(json.dumps(steps))
        for s in steps2:
            if (s.get("type") == "assert" and s.get("scope") == "page"
                    and s.get("assertion") == "page_count"):
                s["expected"] = 2
        spec_bad = generate_spec(steps2, recording["net"],
                                 start_url=origin + "/", name=CASE)
        trace_bad = generate_trace(steps2, recording["net"], name=CASE,
                                   start_url=origin + "/")
        ctx2 = browser.new_context(base_url=origin + "/")
        page2 = ctx2.new_page()
        try:
            _run_spec(spec_bad, page2, "bad.py")
            raise SystemExit("反向验证失败：篡改 page_count 后生成链居然通过")
        except AssertionError as e:
            print(f"pytest chain tampered: FAIL as expected → {str(e)[:140]}")
        exec3 = replay_trace(page2, trace_bad, navigate=False, timeout_ms=5000)
        assert exec3["status"] != "success", "篡改后 trace 链居然通过"
        print("trace chain tampered: FAIL as expected")

        # ── 反向 2：删掉 page_close 步骤再生成 —— 终态必须多一个标签页 ──
        # 证明 page_close 步骤承载真实因果：没有它，回放做出的是另一件事。
        steps3 = [s for s in json.loads(json.dumps(steps))
                  if s.get("type") != "page_close"]
        trace_no_close = generate_trace(steps3, recording["net"], name=CASE,
                                        start_url=origin + "/")
        ctx3 = browser.new_context(base_url=origin + "/")
        page3 = ctx3.new_page()
        page3.goto(origin + "/")
        exec4 = replay_trace(page3, trace_no_close, navigate=False,
                             timeout_ms=5000)
        pages_after = len(page3.context.pages)
        assert pages_after == 2, (
            f"删掉 page_close 后回放终态 {pages_after} 个页面 —— "
            "状态竟与录制一致？该步骤没有承载因果")
        print(f"trace chain without close step: state diverges as expected "
              f"(status={exec4['status']}, pages={pages_after}, recorded=1)")

        browser.close()
    print("ALL GATES PASS")


if __name__ == "__main__":
    main()
