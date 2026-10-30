"""真实 headed 录制 + 双回放 + 正反验证：popup × 网络断言（scope=network）。

场景（quality-log 矩阵缺口 —— 连续五轮遗留，2026-10-30 本轮）：

  1. 真实 headed Chrome 录制：opener 点「write」真实发出 POST /api/save
     （给菜单一个可绑定的请求）→ opener 点链接开 popup（target=_blank）→
     popup 点「quiet」（不发请求的动作，负断言的因果锚）→ 在 **popup 页**
     上右键打开断言菜单，选「最近请求」+「不应发请求(count=0)」绑定那条
     POST → 确认保存。断言步骤落在 popup 页（pageId=popup、afterStepId=
     popup 的 quiet 点击 —— injector 每页独立 steps 数组）。
  2. record.py 生成管线原子落盘 recording.json / trace.json / test_*.py
  3. 三产物断言一致性（rec-assert id 计数与顺序）由 record.py 自检；这里再验
  4. 正向（环境 A，与录制一致，popup 静默）：生成链 + 回放链都通过
  5. 口径隔离（环境 B）：popup 的 quiet 点击改为让 **opener** 发
     GET /api/ping —— 绑的是 POST /api/save，方法不同不计数，两链仍通过
  6. 缺陷反向（环境 C）：popup 的 quiet 点击改为让 **opener** 发
     POST /api/save（弹窗控件回调主窗口发请求，常见 SPA 模式）—— 请求
     发生在观察窗内但来自**观察者页面之外**，两链都必须失败 actual=1
  7. 污染反向（环境 D）：把断言口径篡改成 GET /api/ping，环境 B 的
     ping 真的发 —— 绑错口径的负断言必须如实报 actual=1

本轮修复绑定（D1–D3，配 test/test_p2_popup_network.py 失败测试）：
  D1 rec_helpers.NetRequestWatcher 观察口径升到 **browser context 级** ——
     录制侧（record.py on_request）就是 context 级，popup 里发的请求照常
     进菜单可绑定断言；回放端只看一个 page 时「不应发请求」的「不」被
     观察窗口缩小，观察者页面之外的请求假绿（真实闭环 + 单测双向证实）。
  D2 generate_spec：同动作共存自动写断言（expect_request）与 count=0 时，
     观察者创建行必须落在 with 块**内部**（缩进跟 with 走）—— 修复前
     缩进少一层，生成的 pytest 一执行就 IndentationError（P2.6 风险清单
     第 5 条「count 断言与自动写断言共存」专项覆盖）。
  D3 replay_trace：count=0 的观察者创建提前到**因果动作节点执行前**
     （afterStepId 映射，validate_trace 已保证因果序）—— 修复前在断言
     节点本身才建观察者，因果动作自己/其后触发的请求已经漏掉。

产物在 recordings/p2-popup-network-recording/ 下，与本提交绑定。
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

CASE = "p2-popup-network-recording"
OUT = Path(__file__).resolve().parents[1] / "recordings" / CASE

# quiet 按钮的 onclick 由环境变量注入：
#   A（录制/正向）无 —— 完全静默；
#   B/D —— popup 点 quiet 经服务端信号让 **opener** 发 GET /api/ping
#          （口径隔离 + 污染反向用它）；
#   C —— popup 点 quiet 经服务端信号让 **opener** 发 POST /api/save
#        （缺陷反向：观察窗内、观察者页面之外的违规请求）。
#
# 「服务端信号」而非 window.opener：Chrome 88+ 对 target=_blank 链接默认
# 隐式 rel=noopener，popup 里 window.opener 恒为 null —— opener.fetch
# 静默失败，反向永远打不响（真实闭环实测）。改成 popup fetch /signal?set=1，
# 服务端记一个触发位；opener 页的轮询脚本拿到信号后自己发违规请求 ——
# 这正是「popup 里的控件触发主窗口发请求」的真实 SPA 形态。
QUIET_FLAG = {"A": None, "B": "ping", "C": "save"}

OPENER_T = """<!doctype html><html><head><meta charset="utf-8">
<title>opener</title></head><body>
<button id="save" onclick="setTimeout(function(){fetch('/api/save',{method:'POST',body:'x=1'})},300)">write</button>
<a id="open" href="/popup" target="_blank">open popup</a>
<div id="marker">opener</div>
__POLL__
</body></html>"""

POLL_JS = """<script>
setInterval(function(){fetch('/signal').then(function(r){return r.text()})
  .then(function(k){if(k==='ping')fetch('/api/ping',{method:'GET'});
                   if(k==='save')fetch('/api/save',{method:'POST',body:'late=1'});})},200);
</script>"""

POPUP_T = """<!doctype html><html><head><meta charset="utf-8">
<title>popup</title></head><body>
<button id="quiet" onclick="fetch('/signal?set=__FLAG__')">quiet</button>
<div id="marker">popup</div>
</body></html>"""


def _serve(env: str):
    flag = QUIET_FLAG[env] or "none"

    state = {"signal": ""}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/signal"):
                if "set=" in self.path:
                    state["signal"] = self.path.split("set=", 1)[1]
                    body = b"ok"
                else:
                    body = state["signal"].encode()
                    state["signal"] = ""           # 信号取走即清（只发一次）
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/ping":
                body = b'{"pong": true}'
                ctype = "application/json"
            elif self.path.startswith("/popup"):
                body = POPUP_T.replace("__FLAG__", flag).encode()
                ctype = "text/html; charset=utf-8"
            else:
                # 录制环境（A）不带轮询脚本：菜单清单里不掺 /signal 噪音
                body = OPENER_T.replace(
                    "__POLL__", POLL_JS if flag != "none" else "").encode()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
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


def drive(page, pump):
    """on_ready 回调：真实节奏录下「开 popup 后不应再发 POST」的负断言。

    1) opener 点 #save 真实发出 POST /api/save（菜单清单里有可绑定的请求
       —— 菜单红线：没有可选请求就禁保存）；
    2) opener 点链接开 popup（target=_blank 真实新标签页）；
    3) popup 点 #quiet（不发请求的动作 —— 观察窗的因果锚）；
    4) 在 **popup 页**上右键打开断言菜单 → 最近请求 → 不应发请求(count=0)
       → 绑定清单里的 POST → 确认。这一步在 popup 上落盘：pageId=popup、
       afterStepId=popup 的 quiet 点击。
    """
    page.bring_to_front()
    pump()
    # 1) opener 真实写请求（可绑定）
    page.click("#save")
    page.wait_for_timeout(700)              # POST 发出 + 响应回到最近清单
    pump()
    # 2) opener 开 popup
    with page.context.expect_page() as popup_info:
        page.click("#open")
    popup = popup_info.value
    popup.wait_for_load_state("load")
    pump()
    # 3) popup 上的静默动作（观察窗因果锚）
    popup.click("#quiet")
    popup.wait_for_timeout(300)
    pump()
    # 4) popup 页上开菜单录 count=0 断言
    box = popup.locator("#marker").bounding_box()
    popup.mouse.click(box["x"] + box["width"] / 2,
                      box["y"] + box["height"] / 2, button="right")
    menu = popup.frame_locator("#__rec_assert_menu__")
    menu.locator("#sc").wait_for(state="visible")
    menu.locator("#sc").select_option("network")
    menu.locator("#t").select_option("request_count")
    popup.wait_for_function(
        """() => {
          const f = document.querySelector('#__rec_assert_menu__');
          if (!f) return false;
          const doc = f.contentDocument || f.contentWindow.document;
          const sel = doc.getElementById('netReq');
          return !!sel && [...sel.options].some(o => o.value);
        }""", timeout=10_000)
    options = menu.locator("#netReq option")
    picked = False
    for i in range(options.count()):
        value = options.nth(i).get_attribute("value") or ""
        if '"POST"' in value:
            menu.locator("#netReq").select_option(value)
            picked = True
            break
    assert picked, "最近请求清单里没有 POST 请求可绑定"
    popup.wait_for_timeout(200)
    ok = menu.get_by_role("button", name="添加断言")
    assert ok.is_enabled(), "count=0 断言应当可保存（已绑定请求）"
    ok.click()
    popup.wait_for_timeout(400)
    pump()


def _run_spec(spec_text, page, tag):
    ns = {"__file__": str(OUT / f"test_{CASE}.py")}
    exec(compile(spec_text, tag, "exec"), ns)
    return next(v for k, v in ns.items()
                if k.startswith("test_") and callable(v))


def _retarget(steps, origin):
    """把录制步骤的 pageOrigin/reqUrl 重定向到新 origin（回放环境换了端口）。"""
    out = json.loads(json.dumps(steps))
    for s in out:
        if s.get("pageOrigin"):
            s["pageOrigin"] = origin
        if s.get("reqUrl") is not None:
            s["reqUrl"] = origin + "/api/save"
    return out


def _retarget_net(net, origin):
    """网络事件同样重定向 —— 回放生成的 spec 必须带着自动写断言等待块
    （expect_request），不然 #write 的 POST 会在回放节奏下「迟到」：
    生成链不等待它，POST 拖到 popup 打开之后才发，落进观察窗，正向
    假红（录制时驱动等了 700ms，回放链没有这个等待就不等价）。"""
    out = json.loads(json.dumps(net))
    for n in out:
        if n.get("url"):
            n["url"] = origin + "/api/save"
    return out


def _replay_both_chains(browser, steps, net, origin, tag):
    """同一份 steps+net 在新 origin 上生成两链并各自回放。

    spec 结果：None = 通过；(异常类型, 消息) = 失败。
    trace 结果：execution dict（看 status）。
    """
    spec = generate_spec(steps, net, start_url=origin + "/", name=CASE)
    compile(spec, f"{tag}.py", "exec")      # 产码必须语法成立
    trace = generate_trace(steps, net, name=CASE, start_url=origin + "/")
    ctx = browser.new_context(base_url=origin + "/")
    try:
        page = ctx.new_page()
        spec_result = None
        try:
            _run_spec(spec, page, f"{tag}.py")(page)
        except Exception as e:              # noqa: BLE001 —— 负例也走这里
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
    srv, origin = _serve("A")
    try:
        # ── 1) 真实 headed 录制 ──
        result = record_session(
            start_url=origin + "/", name=CASE,
            out_dir=str(OUT.parent), state_dir="/tmp/rec-p2-popup-net-state",
            on_ready=drive,
        )
        print("record ok:", result["raw_file"])

        recording = json.loads(Path(result["raw_file"]).read_text())
        steps = recording["steps"]
        # 录制产物核验 1：count=0 断言落在 popup 页（pageId=popup），
        # 绑定 POST /api/save，因果动作=popup 的 quiet 点击
        popup_page_ids = {s["pageId"] for s in steps
                          if s.get("pageId") and s.get("url") == "/popup"}
        assert popup_page_ids, f"popup 页步骤没录上: {steps}"
        rc = [s for s in steps if s.get("type") == "assert"
              and s.get("scope") == "network"
              and s.get("assertion") == "request_count"]
        assert rc, f"count=0 断言未被录制：{[s.get('type') for s in steps]}"
        rc0 = rc[0]
        assert rc0.get("expected") == 0 and rc0.get("expectedConfirmed") is not False
        assert rc0.get("reqMethod") == "POST" and "/api/save" in rc0.get("reqUrl", "")
        assert rc0.get("pageId") in popup_page_ids, (
            f"断言应落在 popup 页: {rc0.get('pageId')} vs {popup_page_ids}")
        quiet_clicks = [s for s in steps if s.get("type") == "click"
                        and s.get("pageId") in popup_page_ids]
        assert quiet_clicks, "popup 页必须有因果动作步骤（quiet 点击）"
        assert rc0.get("afterStepId") == quiet_clicks[-1]["id"], (
            f"因果动作应是 popup 的 quiet 点击: {rc0.get('afterStepId')}")
        print("recorded assertion ok:", rc0["id"], rc0["reqMethod"],
              rc0["reqUrl"], "after", rc0["afterStepId"],
              "on", rc0["pageId"])

        spec_text = Path(result["spec_file"]).read_text()
        trace = json.loads(Path(result["trace_file"]).read_text())
        # 录制产物核验 2：三产物断言一致性（record.py 已自检，这里再验）
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

            recording_net = recording.get("net") or []
            # ── 正向（环境 A）：生成链 + 回放链 ──
            # 观察窗起点=断言的因果动作（popup quiet 点击）之前，窗内无请求
            # → 两链通过。录制里 #save 的 POST 在窗起点之前完成，不计入。
            spec_a, exec_a = _replay_both_chains(
                browser, _retarget(steps, origin),
                _retarget_net(recording_net, origin), origin, "pos")
            assert spec_a is None, f"环境 A 生成链应通过: {spec_a}"
            assert exec_a["status"] == "success", exec_a["steps"]
            print("positive (env A): pytest PASS + trace PASS")

            # ── 口径隔离（环境 B）：popup 的 quiet 点击让 opener 发
            #    GET /api/ping —— 绑的是 POST，方法不同不计数，两链仍通过
            #    （防「观察口径变大后方法失明」回归）。
            #    服务 B 一直开到污染反向（D）结束 —— D 复用 B 的环境。
            srv_b, origin_b = _serve("B")
            try:
                spec_b, exec_b = _replay_both_chains(
                    browser, _retarget(steps, origin_b),
                    _retarget_net(recording_net, origin_b), origin_b, "iso")
                assert spec_b is None, (
                    f"口径隔离失败：无关 GET 被错记进 POST 口径: {spec_b}")
                assert exec_b["status"] == "success", (
                    f"口径隔离失败：trace 链把 GET ping 错记进 POST 口径: "
                    f"{exec_b['steps']}")
                print("cross-method isolation (env B): PASS (GET ping not counted)")

                # ── 缺陷反向（环境 C）：popup 的 quiet 点击让 **opener** 发
                #    POST /api/save —— 请求在观察窗内、来自观察者页面之外，
                #    两链都必须失败 actual=1（修复前：page 级观察者看不到
                #    opener 的请求，假绿）。
                srv_c, origin_c = _serve("C")
                try:
                    spec_c, exec_c = _replay_both_chains(
                        browser, _retarget(steps, origin_c),
                        _retarget_net(recording_net, origin_c), origin_c,
                        "defect")
                    assert spec_c is not None, (
                        "缺陷反向失败：观察窗外请求（opener 发）居然没让生成链失败")
                    assert "请求数为 0" in spec_c[1] and "actual=1" in spec_c[1], (
                        f"生成链失败信息形态不对: {spec_c[0]}: {spec_c[1][:200]}")
                    assert exec_c["status"] != "success", (
                        "缺陷反向失败：trace 链对 opener 发出的请求假绿（两链不同判）")
                    print(f"defect negative (env C): pytest FAIL as expected → "
                          f"{spec_c[1][:100]}")
                    print("defect negative (env C): trace FAIL as expected")
                finally:
                    srv_c.shutdown()

                # ── 污染反向（环境 D）：口径篡改成 GET /api/ping，环境 B 的
                #    ping 真的发 —— 绑错口径必须如实报 actual=1（污染数据
                #    不得假绿）。
                steps_d = _retarget(steps, origin_b)
                for s in steps_d:
                    if s.get("assertion") == "request_count":
                        s["reqUrl"] = origin_b + "/api/ping"
                        s["reqMethod"] = "GET"
                spec_d, exec_d = _replay_both_chains(
                    browser, steps_d, _retarget_net(recording_net, origin_b),
                    origin_b, "poison")
                assert spec_d is not None, (
                    "污染反向失败：篡改口径后生成链居然通过")
                assert "请求数为 0" in spec_d[1], (
                    f"污染口径失败形态不对: {spec_d[0]}: {spec_d[1][:200]}")
                assert exec_d["status"] != "success", (
                    "污染反向失败：trace 链在污染口径下居然通过")
                print(f"poisoned reqUrl (env D): pytest FAIL as expected → "
                      f"{spec_d[1][:100]}")
                print("poisoned reqUrl (env D): trace FAIL as expected")
            finally:
                srv_b.shutdown()
            browser.close()
        print("ALL GATES PASS")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
