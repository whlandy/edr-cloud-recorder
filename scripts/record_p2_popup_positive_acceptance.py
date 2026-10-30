"""真实 headed 录制 + 双回放 + 正反验证：popup 发起的**正向**网络断言（P2.9）。

场景（补齐 P2.8 剩余风险 #4 —— 正向网络断言的真实 headed 录制证据）：

  1. 真实 headed Chrome 录制：opener 点链接开 popup（target=_blank）→ popup
     加载即发 POST /api/save（body `{"p":"1"}`，响应 201 `{}`）→ 在 **opener 页**
     上右键打开断言菜单，选「最近请求」绑定这条 popup-load 的 POST，逐个加
     status=201 / request_body={"p":"1"} / response_body={} subset 断言 → 确认。
     因果动作 = opener 的 #open 点击（观察起点在动作之后、popup 的请求在窗内）。
  2. record.py 生成管线原子落盘 recording.json / trace.json / test_*.py
  3. 三产物断言 id/数量一致 + pageId/afterStepId 因果一致（record.py 已自检，这里再验）
  4. 正向（生成链 + 回放链都通过）：status=201、request_body、response_body
  5. 篡改反向：status 期望 201→500 → 两链都失败；body 篡改 → 两链都失败
  6. 移除反向：popup 不再发请求（net 无事件 / 服务端安静）→ 两链都失败
     （读取异常不假通过）

观察口径与录制侧一致（P2.8 已修）：popup 从别的 page 发请求，生成/回放两端
context 级观察才能抓到 —— 本脚本用真实 headed 录制验证这条链路的端到端。
"""

import json
import re
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
import trace_schema as ts  # noqa: E402

CASE = "p2-popup-positive-recording"
OUT = Path(__file__).resolve().parents[1] / "recordings" / CASE

# 默认：popup 加载即发 POST。env="quiet"（移除反向用）时不发。
OPENER_T = """<!doctype html><html><head><meta charset="utf-8">
<title>opener</title></head><body>
<a id="open" href="/popup" target="_blank">open popup</a>
<div id="marker">opener</div>
</body></html>"""

QUIET_POPUP = """<!doctype html><html><head><meta charset="utf-8">
<title>popup</title></head><body>
<div id="marker">popup</div>
</body></html>"""

FIRING_POPUP = """<!doctype html><html><head><meta charset="utf-8">
<title>popup</title></head><body>
<div id="marker">popup</div>
<script>setTimeout(function(){fetch('/api/save',{method:'POST',
body:JSON.stringify({p:'1'})})},150)</script>
</body></html>"""


def _serve(env: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/popup"):
                body = (QUIET_POPUP if env == "quiet" else FIRING_POPUP).encode()
            else:
                body = OPENER_T.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def _pick_post(menu_locator):
    """在菜单 netReq 里挑那条 POST /api/save（popup-load 的请求），返回选中值或 None。"""
    from playwright.sync_api import TimeoutError as PWTimeoutError
    options = menu_locator.locator("#netReq option")
    for i in range(options.count()):
        value = options.nth(i).get_attribute("value") or ""
        if '"POST"' in value and "/api/save" in value:
            menu_locator.locator("#netReq").select_option(value)
            return value
    return None


def drive(page, pump):
    """on_ready 回调：真实节奏录「popup-load 的 POST」的三条正向网络断言。"""
    # 1) opener 打开 popup（target=_blank），popup 加载即发 POST /api/save
    page.bring_to_front()
    pump()
    with page.context.expect_page() as popup_info:
        page.click("#open")
    popup = popup_info.value
    popup.wait_for_load_state("load")
    # 等 popup-load 的 POST 进 record.py 的最近请求清单（on_request context 级已抓到）
    page.wait_for_function(
        """() => window.__recNet && window.__recNet().then
              ? window.__recNet().then(r => r.some(x => x.method==='POST'
                  && x.url.includes('/api/save')))
              : (window.__recNet() || []).some(x => x.method==='POST'
                  && x.url.includes('/api/save'))""",
        timeout=8000,
    )
    pump()
    page.wait_for_timeout(300)
    pump()

    # 2) opener 页上开菜单，逐一加三条网络断言（都绑定 popup-load 的 POST）
    def add_network(assertion, expected):
        box = page.locator("#marker").bounding_box()
        page.mouse.click(box["x"] + box["width"] / 2,
                         box["y"] + box["height"] / 2, button="right")
        page.wait_for_timeout(200)
        menu = page.frame_locator("#__rec_assert_menu__")
        menu.locator("#sc").wait_for(state="visible")
        menu.locator("#sc").select_option("network")
        page.wait_for_timeout(200)
        menu.locator("#t").select_option(assertion)
        page.wait_for_timeout(200)
        picked = _pick_post(menu)
        assert picked, "最近请求清单里没有 popup-load 的 POST 可绑定"
        page.wait_for_timeout(150)
        if assertion != "request_count":
            menu.locator("#es").fill(expected)
            page.wait_for_timeout(100)
        ok = menu.get_by_role("button", name="添加断言")
        assert ok.is_enabled(), f"{assertion} 断言应当可保存"
        ok.click()
        page.wait_for_timeout(300)
        pump()

    add_network("status", "201")
    add_network("request_body", '{"p":"1"}')
    add_network("response_body", "{}")


def _run_spec(spec_text, page, tag):
    ns = {"__file__": str(OUT / f"test_{CASE}.py")}
    exec(compile(spec_text, tag, "exec"), ns)
    return next(v for k, v in ns.items()
                if k.startswith("test_") and callable(v))


def _retarget(steps, origin):
    out = json.loads(json.dumps(steps))
    for s in out:
        if s.get("pageOrigin"):
            s["pageOrigin"] = origin
        if s.get("reqUrl") is not None:
            s["reqUrl"] = origin + "/api/save"
    return out


def _retarget_net(net, origin):
    out = json.loads(json.dumps(net))
    for n in out:
        if n.get("url"):
            n["url"] = origin + "/api/save"
    return out


def _replay_both_chains(browser, steps, net, origin, tag):
    spec = generate_spec(steps, net, start_url=origin + "/", name=CASE)
    compile(spec, f"{tag}.py", "exec")
    trace = generate_trace(steps, net, name=CASE, start_url=origin + "/")
    ctx = browser.new_context(base_url=origin + "/")
    try:
        page = ctx.new_page()
        spec_result = None
        try:
            _run_spec(spec, page, f"{tag}.py")(page)
        except Exception as e:              # noqa: BLE001
            spec_result = (type(e).__name__, str(e))
        page2 = ctx.new_page()
        page2.goto(origin + "/")
        execution = replay_trace(page2, trace, navigate=False, timeout_ms=8000)
        return spec_result, execution
    finally:
        ctx.close()


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    srv, origin = _serve("firing")
    try:
        # ── 1) 真实 headed 录制 ──
        result = record_session(
            start_url=origin + "/", name=CASE,
            out_dir=str(OUT.parent), state_dir="/tmp/rec-p2-popup-positive-state",
            on_ready=drive,
        )
        print("record ok:", result["raw_file"])
        recording = json.loads(Path(result["raw_file"]).read_text())
        steps = recording["steps"]
        net = recording.get("net") or []

        # ── 录制产物核验 1：三条网络断言的归属/因果/期望 ──
        na = [s for s in steps if s.get("type") == "assert"
              and s.get("scope") == "network"]
        by_assert = {s["assertion"]: s for s in na}
        assert set(by_assert) >= {"status", "request_body", "response_body"}, by_assert
        opener_page_id = next(s["pageId"] for s in steps
                              if s["id"] == by_assert["status"]["afterStepId"])
        for kind, exp in (("status", 201), ("request_body", {"p": "1"}),
                          ("response_body", {})):
            s = by_assert[kind]
            assert s["expected"] == exp, (kind, s["expected"], exp)
            assert s.get("expectedConfirmed") is not False, s
            assert s.get("reqMethod") == "POST" and "/api/save" in (s.get("reqUrl") or ""), s
            assert s.get("pageId") == opener_page_id, s
        # 因果：afterStepId 指向 opener 的 #open 点击，且该点击页=opener、t 在请求前
        opener_click = next((s for s in steps if s["id"] == by_assert["status"]["afterStepId"]), None)
        assert opener_click and opener_click["type"] == "click", \
            f"afterStepId 应指向 opener 的 #open 点击: {by_assert['status']['afterStepId']}"
        assert opener_click["pageId"] == opener_page_id
        req_t = next((n["t"] for n in net if n["phase"] == "req"), 10**9)
        assert opener_click["t"] < req_t < by_assert["status"]["t"], \
            (opener_click["t"], req_t, by_assert["status"]["t"])
        print("recorded asserts ok:", {k: (v["id"], v["afterStepId"]) for k, v in by_assert.items()})

        # ── 录制产物核验 2：三产物断言 id/数量一致 ──
        spec_text = Path(result["spec_file"]).read_text()
        trace = json.loads(Path(result["trace_file"]).read_text())
        spec_ids = re.findall(r"^ *# rec-assert: (\S+)", spec_text, re.M)
        rec_ids = [s["id"] for s in steps if s.get("type") == "assert" and s.get("id")]
        tr_ids = [ts.provenance(n).get("sourceStepId")
                  for _, n in ts.nodes(trace) if ts.assertion_of(n)]
        assert spec_ids == rec_ids == tr_ids, (spec_ids, rec_ids, tr_ids)
        assert ts.meta(trace).get("status") == "ready", ts.meta(trace).get("status")
        print("artifact consistency ok:", spec_ids)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())
            try:
                # ── 正向：生成链 + 回放链都通过 ──
                spec_p, exec_p = _replay_both_chains(
                    browser, _retarget(steps, origin), _retarget_net(net, origin),
                    origin, "pos")
                assert spec_p is None, f"正向生成链应通过: {spec_p}"
                assert exec_p["status"] == "success", exec_p["steps"]
                print("positive (firing env): pytest PASS + trace PASS")

                # ── 篡改反向：status 期望 201→500，两链都失败 ──
                steps_tamper = _retarget(steps, origin)
                for s in steps_tamper:
                    if s.get("assertion") == "status":
                        s["expected"] = 500
                spec_t, exec_t = _replay_both_chains(
                    browser, steps_tamper, _retarget_net(net, origin), origin, "tam")
                assert spec_t is not None, "篡改 status 后生成链居然通过"
                assert exec_t["status"] != "success", "篡改 status 后 trace 链假绿"

                # ── 篡改反向：body 期望篡改，两链都失败 ──
                steps_b = _retarget(steps, origin)
                for s in steps_b:
                    if s.get("assertion") == "request_body":
                        s["expected"] = {"p": "2"}
                spec_b, exec_b = _replay_both_chains(
                    browser, steps_b, _retarget_net(net, origin), origin, "tamb")
                assert spec_b is not None, "篡改 body 后生成链居然通过"
                assert exec_b["status"] != "success", "篡改 body 后 trace 链假绿"
                print("tamper reverses: status 201->500 FAIL, body {p:1}->{p:2} FAIL (both chains)")

                # ── 移除反向：popup 不发请求（quiet 环境）──
                # 断言绑定的请求根本不出现 → 两链都失败（读取异常不假通过）。
                srv_q, origin_q = _serve("quiet")
                try:
                    spec_q, exec_q = _replay_both_chains(
                        browser, _retarget(steps, origin_q),
                        _retarget_net(net, origin_q), origin_q, "quiet")
                    assert spec_q is not None, "popup 不发请求时生成链居然通过"
                    assert exec_q["status"] != "success", "popup 不发请求时 trace 链假绿"
                    print("remove-request reverse: quiet env FAIL (both chains)")
                finally:
                    srv_q.shutdown()
            finally:
                browser.close()
        print("ALL GATES PASS")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
