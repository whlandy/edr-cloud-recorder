"""真实 headed 录制 + 双回放 + 正反验证：同主机不同端口 / 跨 origin。

场景（quality-log 矩阵「同主机不同端口 / 跨 origin」缺口，2026-10-30 本轮）：

  1. 真实 headed Chrome 录制：A 端口填值（blur 落账）→ 点按钮
     （setTimeout 800ms 后跳 B 端口同路径 /）→ B 加载完成 → B 填值
     → B 上断言输入值
  2. record.py 生成管线原子落盘 recording.json / trace.json / test_*.py
  3. 生成链（pytest 函数真实执行）：必须等待跨 origin 地址变化（本轮
     D1 修复），fill/断言全部落在 B 页面上，正向通过
  4. 回放链（replay_trace）：_page_for_node 按 pageOrigin 校验，正向通过
  5. 反向 1：篡改 B 上断言 expected（world → hello），两条链都必须失败，
     且失败信息指向具体断言
  6. 反向 2：把 B 上 fill 步骤的 pageOrigin 改成 A（模拟 D2 录制缺陷的
     污染数据）再生成 —— 生成链会把 B 的步骤当 A 的步骤转译，状态错位
     必须被暴露（不能静默一致）

本轮修复绑定：
  D1 generate_spec：同页地址变化基准记完整 URL（含 origin）——跨 origin
     同路径导航不再被当成「没动」。
  D2 record + recorder-inject：注入层在事件现场写 pageOrigin，宿主泵结算
     只兜底缺失值 —— 泵间隙里的跨 origin 导航不再污染旧步骤的归属。

产物在 recordings/p2-cross-origin-recording/ 下，与本提交绑定。
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
from generate_spec import generate_spec  # noqa: E402
from generate_trace import generate_trace  # noqa: E402
from replay_trace import replay_trace  # noqa: E402
from record import record_session  # noqa: E402

CASE = "p2-cross-origin-recording"
OUT = Path(__file__).resolve().parents[1] / "recordings" / CASE

PAGE_T = """<!doctype html><html><head><meta charset="utf-8">
<title>origin-fixture</title></head><body>
<input id="q" placeholder="keyword">
<button id="go" onclick="setTimeout(function(){{location.href='{b}/';}},800)">delayed go</button>
<div id="marker">origin-{tag}</div>
</body></html>"""


def _serve():
    state = {"b": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            time.sleep(getattr(self.server, "rec_delay", 0))
            tag = "a"
            if state["b"] and self.headers.get("Host", "").endswith(
                    str(state["b"]).rsplit(":", 1)[-1]):
                tag = "b"
            body = PAGE_T.format(b=state["b"] or "PLACEHOLDER", tag=tag).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    servers = [HTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
    for srv in servers:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    a_srv, b_srv = servers
    a_srv.rec_delay, b_srv.rec_delay = 0.0, 1.0
    a = f"http://127.0.0.1:{a_srv.server_port}"
    b = f"http://127.0.0.1:{b_srv.server_port}"
    state["b"] = b
    return servers, a, b


def drive(page, pump):
    """on_ready 回调：A 填值 → 点延迟跳转按钮 → B 填值 → B 断言。

    输入按真人节奏：点击聚焦、fill、点空白处让 change 落账（Playwright
    的 fill() 不发 change）。断言走录制菜单（B 页面上对 #q 下 value
    equals world 断言，勾显式确认 —— P0 红线）。
    """
    page.bring_to_front()
    pump()
    # 1) A 上填值，blur 落账
    page.click("#q")
    page.fill("#q", "hello")
    page.click("#marker")
    page.wait_for_timeout(300)
    pump()
    # 2) 点延迟跳转按钮（800ms 后跳 B）。点击本身不导航 —— 修复前生成链
    #    会不等跳转就对「B 上的步骤」先行操作。这里不 pump，让 fill 的
    #    身份结算自然落入泵间隙（覆盖 D2 形态：导航发生在结算前）。
    page.click("#go")
    page.wait_for_timeout(400)
    # 3) 等 B 加载完成（B 故意慢 1s），泵在此刻结算 A 的 fill —— page.url
    #    已是 B，修复前会污染 fill 的 pageOrigin
    page.wait_for_load_state("load")
    page.wait_for_timeout(600)
    pump()
    # 4) B 上填值 + blur 落账
    page.click("#q")
    page.fill("#q", "world")
    page.click("#marker")
    page.wait_for_timeout(300)
    pump()
    # 5) B 上对 #q 下 value equals world 断言（显式确认）
    box = page.locator("#q").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2,
                     box["y"] + box["height"] / 2, button="right")
    menu = page.frame_locator("#__rec_assert_menu__")
    menu.locator("#t").wait_for(state="visible")
    menu.locator("#t").select_option("value")
    menu.locator("#op").select_option("equals")
    menu.locator("#es").fill("world")
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
    servers, origin_a, origin_b = _serve()
    try:
        result = record_session(
            start_url=origin_a + "/", name=CASE,
            out_dir=str(OUT.parent), state_dir="/tmp/rec-p2-xorigin-state",
            on_ready=drive,
        )
        print("record ok:", result["raw_file"])

        recording = json.loads(Path(result["raw_file"]).read_text())
        steps = recording["steps"]
        # 录制产物核验 1：A 的 fill 步骤 pageOrigin 必须仍是 A（D2 修复生效）
        fills = [s for s in steps if s.get("type") == "fill"]
        assert len(fills) >= 2, f"fill 步骤不足 2 条：{steps}"
        a_fill = [s for s in fills if s.get("value") == "hello"][0]
        b_fill = [s for s in fills if s.get("value") == "world"][0]
        assert a_fill["pageOrigin"] == origin_a, (
            f"A 的 fill pageOrigin 被污染成 {a_fill['pageOrigin']}")
        assert b_fill["pageOrigin"] == origin_b, (
            f"B 的 fill pageOrigin 应为 {origin_b}，实际 {b_fill['pageOrigin']}")
        # 录制产物核验 2：value 断言已被录制且 pageOrigin 归属 B
        value_asserts = [s for s in steps if s.get("type") == "assert"
                         and s.get("assertion") == "value"]
        assert value_asserts, "B 上的 value 断言未被录制"
        assert value_asserts[0]["pageOrigin"] == origin_b
        print("recorded steps origins ok:",
              [(s["value"], s["pageOrigin"].rsplit(":", 1)[-1]) for s in fills])

        spec_text = Path(result["spec_file"]).read_text()
        # 生成产物核验 3：跨 origin 地址变化必须产生等待（D1 修复生效）
        import re
        waits = re.findall(r"wait_for_recorded_url\(page, ([^)]+)\)", spec_text)
        assert any(origin_b in w for w in waits), (
            f"spec 未包含指向 B 的地址等待：{waits}\n{spec_text}")
        print("spec cross-origin wait:", waits)

        trace = json.loads(Path(result["trace_file"]).read_text())
        meta = trace["$meta"]["attach"]
        assert meta.get("status") == "ready", meta.get("status")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            ctx = browser.new_context(base_url=origin_a + "/")

            # ── 生成链正向 ──
            page = ctx.new_page()
            _run_spec(spec_text, page, f"{CASE}.py")
            assert page.locator("#q").input_value() == "world", (
                "生成链回放后 B 输入框应为 world")
            print("pytest chain positive: PASS (cross-origin wait honored)")

            # ── 回放链正向 ──
            page2 = ctx.new_page()
            page2.goto(origin_a + "/")
            exec2 = replay_trace(page2, trace, navigate=False, timeout_ms=8000)
            assert exec2["status"] == "success", exec2["steps"]
            assert page2.locator("#q").input_value() == "world"
            print("trace chain positive: PASS")

            # ── 反向 1：篡改 expected（world → hello）→ 双链必须失败 ──
            steps2 = json.loads(json.dumps(steps))
            for s in steps2:
                if s.get("type") == "assert" and s.get("assertion") == "value":
                    s["expected"] = "hello"
            spec_bad = generate_spec(steps2, recording["net"],
                                     start_url=origin_a + "/", name=CASE)
            trace_bad = generate_trace(steps2, recording["net"], name=CASE,
                                       start_url=origin_a + "/")
            page3 = ctx.new_page()
            try:
                _run_spec(spec_bad, page3, "bad.py")
                raise SystemExit("反向验证失败：篡改 expected 后生成链居然通过")
            except AssertionError as e:
                msg = str(e)
                assert "rec-assert" in msg or "world" in msg or "hello" in msg, msg
                print(f"pytest chain tampered: FAIL as expected → {msg[:140]}")
            exec3 = replay_trace(page3, trace_bad, navigate=False,
                                 timeout_ms=8000)
            assert exec3["status"] != "success", "篡改后 trace 链居然通过"
            print("trace chain tampered: FAIL as expected")

            # ── 反向 2：把 B 的 fill pageOrigin 篡改成 A（模拟 D2 污染数据）──
            # 生成器会据此把「B 上的后续步骤」转译成对 A 页面的操作 —— 本例
            # 形态是「等待导航回 A」永不发生而超时，或对 A 页面操作后状态
            # 错位。任何失败形态都算暴露；只有「通过且终态与录制一致」才是
            # 假绿，必须拒绝。
            steps3 = json.loads(json.dumps(steps))
            for s in steps3:
                if s.get("type") == "fill" and s.get("value") == "world":
                    s["pageOrigin"] = origin_a
            spec_p = generate_spec(steps3, recording["net"],
                                   start_url=origin_a + "/", name=CASE)
            page4 = ctx.new_page()
            diverged = False
            try:
                _run_spec(spec_p, page4, "poisoned.py")
                # spec 竟然通过：只有终态与录制一致才是假绿
                if page4.locator("#q").input_value() != "world":
                    diverged = True
                    print("poisoned spec passed but state diverges "
                          "(input != world) — exposed")
            except Exception as e:                      # noqa: BLE001
                # 超时/断言/定位失败都算：污染数据没有被静默放过
                diverged = True
                print(f"pytest chain poisoned-origin: FAIL as expected → "
                      f"{type(e).__name__}: {str(e)[:120]}")
            assert diverged, "污染数据未产生可观察的状态错位"
            print("poisoned pageOrigin → state diverges as expected")

            browser.close()
        print("ALL GATES PASS")
    finally:
        for srv in servers:
            srv.shutdown()


if __name__ == "__main__":
    main()
