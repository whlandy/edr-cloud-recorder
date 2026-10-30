"""真实 headed 录制 + 双回放 + 正反验证：慢写响应（P2.11）。

场景（quality-log P2.10 风险 #3 收窄后的真实形态，2026-10-31 本轮）：

  1. 真实 headed Chrome 录制：点 #save 发 POST /api/save，服务器**延迟
     7s** 才回 201；再在断言菜单选「最近请求」对这条 POST 下 status=201
     的用户断言（expectedConfirmed=true）。
  2. record.py 生成管线原子落盘 recording.json / trace.json / test_*.py。
  3. 生成链（pytest 函数真实执行）：context.expect_event 缺省 30s + 无
     上限的 response() —— 7s 慢响应照常等到，正向通过。
  4. 回放链（replay_trace）：响应等待必须用同源的网络响应预算
     （NET_RESPONSE_WAIT_MS=30s）。修复前用单步定位预算 5s，在同一录制
     上 trace 链 5.0s TimeoutError —— 双链分歧（test_p2_slow_response
     先红证明）。
  5. 反向 1：篡改用户断言 expected（201 → 500），两条链都必须明确失败。
  6. 反向 2：禁用修复（NET_RESPONSE_WAIT_MS → 5_000，等价回单步预算）
     重放 trace 链，必须以 TimeoutError 失败 —— 证明真实录制产物确实
     暴露了被修复的缺陷（录制↔回放节奏差是真实的，不是测试构造的）。

产物在 recordings/p2-slow-response-recording/ 下；recordings/ 按仓库策略
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

CASE = "p2-slow-response-recording"
OUT = Path(__file__).resolve().parents[1] / "recordings" / CASE

SLOW_RESP_DELAY = 7.0

PAGE_MAIN = """<!doctype html><html><head><meta charset="utf-8">
<title>slow-resp-main</title></head><body>
<button id="save" onclick="fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({p:1})})">save</button>
<div id="marker">page-main</div>
</body></html>"""


def _serve():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            if self.path.startswith("/api/save"):
                time.sleep(SLOW_RESP_DELAY)
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            body = b"{}"
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            body = PAGE_MAIN.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def drive(page, pump):
    """on_ready 回调：真实 headed 页面上操作一遍慢响应流。

    节奏与真人一致：点 #save（POST 发出，7s 后 201 才回）→ 等响应完成
    → 右键开断言菜单选「最近请求」对这条 POST 下 status=201 断言。
    """
    page.bring_to_front()
    pump()
    # 1) 点 save：POST 立即发出，服务器 7s 后才回 201
    page.click("#save")
    # 等慢响应真正完成（轮询菜单可见的请求清单，这里直接等固定时长 +
    # pump，让 net 事件落盘）
    page.wait_for_timeout(int(SLOW_RESP_DELAY * 1000) + 500)
    pump()
    # 2) 右键开断言菜单：选「最近请求」范围 → 绑定这条 POST →
    #    响应状态码 status=201 → 确认（P0 红线）
    box = page.locator("#save").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2,
                     box["y"] + box["height"] / 2, button="right")
    menu = page.frame_locator("#__rec_assert_menu__")
    menu.locator("#sc").wait_for(state="visible")
    menu.locator("#sc").select_option("network")
    popup_wait = page.wait_for_function(
        """() => {
          const f = document.querySelector('#__rec_assert_menu__');
          if (!f) return false;
          const doc = f.contentDocument || f.contentWindow.document;
          const sel = doc.getElementById('netReq');
          return !!sel && [...sel.options].some(o => o.value);
        }""", timeout=10_000)
    popup_wait
    options = menu.locator("#netReq option")
    picked = False
    for i in range(options.count()):
        value = options.nth(i).get_attribute("value") or ""
        if '"POST"' in value:
            menu.locator("#netReq").select_option(value)
            picked = True
            break
    assert picked, "最近请求清单里没有 POST 请求可绑定"
    menu.locator("#t").select_option("status")
    menu.locator("#es").fill("201")
    # net-status 的确认语义：用户亲手填 expected 即显式确认（injector 对
    # 非 string/bool 类型 confirmed=true；#confirm 框只在 string 下显示，
    # 对它 check 会 30s 超时 —— P2.10 轮 #es 先于 #confirm 的教训同源）
    page.wait_for_timeout(200)
    ok = menu.get_by_role("button", name="添加断言")
    assert ok.is_enabled(), "status 断言应当可保存（已绑定请求）"
    ok.click()
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
        out_dir=str(OUT.parent), state_dir="/tmp/rec-p2-slow-resp-state",
        on_ready=drive,
    )
    print("record ok:", result["raw_file"])

    recording = json.loads(Path(result["raw_file"]).read_text())
    steps = recording["steps"]
    net = recording["net"]
    print("recorded steps:",
          [(s.get("type"), (s.get("sel") or s.get("assertion") or ""),
            s.get("url")) for s in steps])
    # 录制核验 1：#save 点击落盘（css 兜底含 button#save，无歧义标记）
    clicks = [s for s in steps if s.get("type") == "click"
              and "#save" in (s.get("css") or s.get("sel") or "")]
    assert clicks, f"录制步骤缺 #save 点击：{steps}"
    # 录制核验 2：net 里有这条慢 POST（req + res 配对，201）
    saves = [n for n in net if "/api/save" in n.get("url", "")]
    assert any(n["phase"] == "req" for n in saves), saves
    res = [n for n in saves if n["phase"] == "res"]
    assert res and res[0]["status"] == 201, saves
    # 录制核验 3：status=201 用户断言落盘且显式确认，因果动作是 #save 点击
    status_asserts = [s for s in steps if s.get("type") == "assert"
                      and s.get("scope") == "network"
                      and s.get("assertion") == "status"]
    assert status_asserts, "status 断言未被录制"
    sa = status_asserts[0]
    assert sa.get("expected") == 201 and sa.get("expectedConfirmed") is True, sa
    assert sa.get("afterStepId") == clicks[0]["id"], sa
    print("recording artifact checks: PASS (slow POST + confirmed status assert)")

    # 录制核验 4：慢响应真的落进了录制节奏（res 时间 - req 时间 ≥ 6s）
    req_t = next(n["t"] for n in saves if n["phase"] == "req")
    res_t = res[0]["t"]
    assert res_t - req_t >= SLOW_RESP_DELAY * 1000 - 500, (req_t, res_t)
    print(f"slow-response causality: PASS (req→res gap {(res_t - req_t) / 1000:.1f}s)")

    trace = json.loads(Path(result["trace_file"]).read_text())
    meta = trace["$meta"]["attach"]
    assert meta.get("status") == "ready", meta.get("status")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
                                     executable_path=resolve_chrome())
        ctx = browser.new_context(base_url=origin + "/")

        # ── 生成链正向：7s 慢响应必须等到 ──
        page = ctx.new_page()
        _run_spec(Path(result["spec_file"]).read_text(), page, f"{CASE}.py")
        print("pytest chain positive: PASS (slow write response waited)")

        # ── 回放链正向：NET_RESPONSE_WAIT_MS 预算 ──
        page2 = ctx.new_page()
        page2.goto(origin + "/")
        t0 = time.monotonic()
        exec2 = replay_trace(page2, trace, navigate=False, timeout_ms=5_000)
        elapsed = time.monotonic() - t0
        assert exec2["status"] == "success", exec2["steps"]
        print(f"trace chain positive: PASS (slow response waited, {elapsed:.1f}s)")

        # ── 反向 1：篡改用户断言 expected（201 → 500）→ 双链都失败 ──
        steps2 = json.loads(json.dumps(steps))
        for s in steps2:
            if s.get("type") == "assert" and s.get("scope") == "network" \
                    and s.get("assertion") == "status":
                s["expected"] = 500
        from generate_spec import generate_spec  # noqa: E402
        from generate_trace import generate_trace  # noqa: E402
        spec_bad = generate_spec(steps2, net, start_url=origin + "/",
                                 name=CASE)
        trace_bad = generate_trace(steps2, net, name=CASE,
                                   start_url=origin + "/")
        page3 = ctx.new_page()
        page3.goto(origin + "/")
        try:
            _run_spec(spec_bad, page3, "bad.py")
            raise SystemExit("反向验证失败：篡改 expected 后生成链居然通过")
        except (AssertionError, Exception) as e:
            if "反向验证失败" in str(e):
                raise
            print(f"pytest chain tampered: FAIL as expected → "
                  f"{type(e).__name__}: {str(e)[:140]}")
        page4 = ctx.new_page()
        page4.goto(origin + "/")
        exec3 = replay_trace(page4, trace_bad, navigate=False,
                             timeout_ms=5_000)
        assert exec3["status"] != "success", "篡改后 trace 链居然通过"
        print("trace chain tampered: FAIL as expected")

        # ── 反向 2：禁用修复重放 —— trace 链必须以 TimeoutError 失败 ──
        # 证明真实录制产物确实暴露了被修复的缺陷（响应等待预算差）。
        import replay_trace as rt  # noqa: E402
        saved = rt.NET_RESPONSE_WAIT_MS
        try:
            rt.NET_RESPONSE_WAIT_MS = 5_000
            page5 = ctx.new_page()
            page5.goto(origin + "/")
            exec4 = replay_trace(page5, trace, navigate=False,
                                 timeout_ms=5_000)
            assert exec4["status"] != "success", (
                "禁用修复后 trace 链居然通过 —— 录制产物没有暴露节奏差缺陷？")
            errors = [s.get("error", "") for s in exec4["steps"]
                      if s.get("status") == "failed"]
            assert any("Timeout" in e for e in errors), errors
            print("trace chain fix-disabled: FAIL as expected (TimeoutError, "
                  "缺陷形态与单测红灯一致)")
        finally:
            rt.NET_RESPONSE_WAIT_MS = saved

        browser.close()
    print("ALL GATES PASS")


if __name__ == "__main__":
    main()
