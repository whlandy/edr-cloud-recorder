"""真实 headed 录制 + 双回放 + 正反验证：request_count=0 有界观察窗。

场景（quality-log 矩阵「request_count=0 真实录制闭环」缺口 —— 连续四轮只有
单元证据，2026-10-30 本轮）：

  1. 真实 headed Chrome 录制：点「写」按钮（真实发出 POST /api/save）→
     点「净零检查」按钮（onclick 为 void 0，观察窗内不应有任何请求）→
     菜单选「最近请求」范围 + 「不应发请求(count=0)」，绑定最近那条
     POST 请求 → 确认保存
  2. record.py 生成管线原子落盘 recording.json / trace.json / test_*.py
  3. 三产物断言一致性（rec-assert id 计数与顺序）由 record.py 自检；这里再验：
     pytest 与 trace 都携带同一条 request_count 断言
  4. 生成链正向（pytest 函数真实执行）：观察窗内无匹配请求 → 通过
  5. 回放链正向（replay_trace）：同样通过 —— 两链同判
  6. 反向 1：服务端换成「点了会发请求」的按钮 → 双链都必须失败，
     失败信息带「请求数为 0 断言失败 ... actual=1」
  7. 反向 2：篡改断言的 reqUrl（改成观察窗内真实出现的 URL）→ 双链失败
     （模拟录制数据污染：绑错口径，负断言必须如实报错而不是静默放过）

本轮修复绑定：
  D1 generate_spec：request_count=0 产出「观察者创建（动作前）+ 稳定窗轮询
     （动作后）」，观察者由模板定义（NetRequestWatcher）并导入
     request_count_zero —— 修复前引用未定义的 _net_watcher，生成的 pytest
     一执行就 NameError。
  D2 rec_helpers.NetRequestWatcher.count() 驱动事件分发 —— sync Playwright
     的 request 事件在 time.sleep 轮询期间不分发，观察者数恒 0，负断言假绿。
  D3 replay_trace：scope=network 的 request_count 断言在回放链真正执行有界
     观察 —— 修复前该分支直接放行（verifier no-op），trace 链恒假绿。
  D4 generate_trace：afterStepId 指向不存在步骤的网络断言拒发整条 trace ——
     修复前静默丢断言，与 generate_spec 不同判。

产物在 recordings/p2-request-count-zero-recording/ 下，与本提交绑定。
"""

import json
import re
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
import trace_schema as ts  # noqa: E402

CASE = "p2-request-count-zero-recording"
OUT = Path(__file__).resolve().parents[1] / "recordings" / CASE

# fire=True：#save 点击发 POST /api/save（延迟 300ms —— 观察窗必须数到迟到
# 请求）；#check 是净零检查按钮 —— 对 /api/save 而言它不发请求（fire=False）
# 或只发一个不同端点的 GET /api/ping（fire=True，用于反向 2 的污染口径：
# 绑错端点的负断言必须抓住真的被发出的 ping）。fire=False 时 #check 完全静默。
PAGE_T = """<!doctype html><html><head><meta charset="utf-8">
<title>request-count-fixture</title></head><body>
<button id="save" onclick="__SAVE_JS__">write</button>
<button id="check" onclick="__CHECK_JS__">net zero check</button>
<input id="q" placeholder="keyword">
<div id="marker">x</div>
</body></html>"""


def _save_js(fire: bool, delay_ms: int) -> str:
    if not fire:
        return "void 0"
    return ("setTimeout(function(){fetch('/api/save',{method:'POST',"
            "body:'x=1'})}," + str(delay_ms) + ")")


def _check_js(fire: bool, delay_ms: int) -> str:
    if not fire:
        return "void 0"
    return ("setTimeout(function(){fetch('/api/ping',{method:'GET'})},"
            + str(delay_ms) + ")")


def _serve(*, fire: bool = True, delay_ms: int = 300):
    page_html = (PAGE_T
                 .replace("__SAVE_JS__", _save_js(fire, delay_ms))
                 .replace("__CHECK_JS__", _check_js(fire, delay_ms)))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(201)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_GET(self):
            if self.path == "/api/ping":
                body = b'{"pong": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = page_html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def drive(page, pump):
    """on_ready 回调：真实操作节奏下录下「不应发请求」断言。

    1) 点 #save 真实发出 POST（这样菜单的最近请求清单里有可绑定的请求 ——
       菜单红线：没有可选请求就禁保存）；
    2) 等请求完成（POST 响应 201 落进清单，标签上带状态码）；
    3) 点 #check（不发请求的动作 —— 观察窗的因果动作）；
    4) 右键打开断言菜单 → 范围=最近请求 → 类型=不应发请求(count=0) →
       绑定清单里的 POST 请求 → 保存（net-zero 无期望值，确认框默认勾选语义
       由菜单自身把关）。
    """
    page.bring_to_front()
    pump()
    # 1) 真实写请求（可绑定）
    page.click("#save")
    page.wait_for_timeout(700)              # POST 发出 + 响应回到最近清单
    pump()
    # 2) 净零检查动作（观察窗的因果动作）
    page.click("#check")
    page.wait_for_timeout(300)
    pump()
    # 3) 菜单：最近请求范围 + count=0，绑定 POST /api/save
    box = page.locator("#check").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2,
                     box["y"] + box["height"] / 2, button="right")
    menu = page.frame_locator("#__rec_assert_menu__")
    menu.locator("#sc").wait_for(state="visible")
    menu.locator("#sc").select_option("network")
    menu.locator("#t").select_option("request_count")
    # 绑定清单里的 POST（清单由 fillNetRequests 的 Promise 拉取，最近请求
    # 倒序在最前 —— 本场景只有一条请求，就是它）。占位项 value 为空串，
    # Promise 返回前清单只有占位 —— 必须等到出现非空 value 的选项。
    page.wait_for_function(
        """() => {
          const f = document.querySelector('#__rec_assert_menu__');
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
    page.wait_for_timeout(200)
    ok = menu.get_by_role("button", name="添加断言")
    assert ok.is_enabled(), "count=0 断言应当可保存（已绑定请求）"
    ok.click()
    page.wait_for_timeout(400)
    pump()


def _run_spec(spec_text, page, tag):
    ns = {"__file__": str(OUT / f"test_{CASE}.py")}
    exec(compile(spec_text, tag, "exec"), ns)
    return next(v for k, v in ns.items()
                if k.startswith("test_") and callable(v))


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    srv, origin = _serve(fire=True)
    quiet_srv, quiet_origin = _serve(fire=False)
    try:
        # ── 1) 真实 headed 录制 ──
        result = record_session(
            start_url=origin + "/", name=CASE,
            out_dir=str(OUT.parent), state_dir="/tmp/rec-p2-rczero-state",
            on_ready=drive,
        )
        print("record ok:", result["raw_file"])

        recording = json.loads(Path(result["raw_file"]).read_text())
        steps = recording["steps"]
        # 录制产物核验 1：request_count 断言已被录制，绑定 POST 与因果动作
        rc = [s for s in steps if s.get("type") == "assert"
              and s.get("scope") == "network"
              and s.get("assertion") == "request_count"]
        assert rc, f"count=0 断言未被录制：{[s.get('type') for s in steps]}"
        assert rc[0].get("expected") == 0 and rc[0].get("expectedConfirmed") is not False
        assert rc[0].get("reqMethod") == "POST" and "/api/save" in rc[0].get("reqUrl", "")
        assert rc[0].get("afterStepId"), "count=0 断言缺因果动作归属"
        action_ids = {s["id"] for s in steps if s.get("type") != "assert"}
        assert rc[0]["afterStepId"] in action_ids, (
            f"afterStepId 悬空: {rc[0]['afterStepId']}")
        print("recorded assertion ok:", rc[0]["id"], rc[0]["reqMethod"],
              rc[0]["reqUrl"], "after", rc[0]["afterStepId"])

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

            # ── 正向 1：生成链（录制时页面，点了 save 会在**观察窗外**发请求；
            #    观察窗从 #check 动作前开始，watcher 创建于该动作前 —— save 的
            #    请求在窗起点之前，不计入）──
            ctx = browser.new_context(base_url=origin + "/")
            page = ctx.new_page()
            test_fn = _run_spec(spec_text, page, f"{CASE}.py")
            test_fn(page)
            print("pytest chain positive: PASS")

            # ── 正向 2：回放链（同一 trace）──
            page2 = ctx.new_page()
            page2.goto(origin + "/")
            exec2 = replay_trace(page2, trace, navigate=False, timeout_ms=8000)
            assert exec2["status"] == "success", exec2["steps"]
            print("trace chain positive: PASS")
            ctx.close()

            # ── 反向 1：服务端换成「净零检查也会发请求」—— 负断言必须抓住 ──
            # （net 事件带着录制 origin，换环境后不再复用 —— count=0 断言
            # 不依赖 net 事件配对，steps 自足。）
            bad_srv, bad_origin = _serve(fire=True, delay_ms=150)
            bad_trace = generate_trace(
                _retarget(steps, bad_origin), [], name=CASE,
                start_url=bad_origin + "/")
            bad_spec = generate_spec(
                _retarget(steps, bad_origin), [], start_url=bad_origin + "/",
                name=CASE)
            ctxb = browser.new_context(base_url=bad_origin + "/")
            pageb = ctxb.new_page()
            pageb.goto(bad_origin + "/")
            try:
                bad_fn = _run_spec(bad_spec, pageb, "bad.py")
                bad_fn(pageb)
            except Exception:
                pass                      # 生成链失败是期望形态，下面统一验
            # 重新走一遍拿到干净失败信息
            pageb2 = ctxb.new_page()
            pageb2.goto(bad_origin + "/")
            try:
                bad_fn2 = _run_spec(bad_spec, pageb2, "bad.py")
                bad_fn2(pageb2)
                raise SystemExit("反向验证失败：篡改页面行为后生成链居然通过")
            except AssertionError as e:
                msg = str(e)
                assert "请求数为 0" in msg and "actual=1" in msg, (
                    f"生成链失败信息形态不对: {msg[:200]}")
                print(f"pytest chain negative (fired): FAIL as expected → {msg[:120]}")
            execb = replay_trace(pageb2, bad_trace, navigate=False,
                                 timeout_ms=8000)
            assert execb["status"] != "success", "篡改后 trace 链居然通过"
            print("trace chain negative (fired): FAIL as expected")
            ctxb.close()
            bad_srv.shutdown()

            # ── 反向 2：篡改 reqUrl 口径 —— 模拟录制数据污染 ──
            # 口径指向另一个真实存在的端点（GET /api/ping，fire 环境里因果
            # 动作 #check 会真的发出它）：绑错口径的负断言必须如实报
            # actual=1，而不是静默放行。
            poison_srv, poison_origin = _serve(fire=True, delay_ms=100)
            steps3 = _retarget(json.loads(json.dumps(steps)), poison_origin)
            for s in steps3:
                if (s.get("type") == "assert"
                        and s.get("assertion") == "request_count"):
                    s["reqUrl"] = poison_origin + "/api/ping"
                    s["reqMethod"] = "GET"
            poisoned = generate_trace(
                steps3, [], name=CASE, start_url=poison_origin + "/")
            poisoned_spec = generate_spec(
                steps3, [], start_url=poison_origin + "/", name=CASE)
            ctxp = browser.new_context(base_url=poison_origin + "/")
            pagep = ctxp.new_page()
            pagep.goto(poison_origin + "/")
            try:
                poison_fn = _run_spec(poisoned_spec, pagep, "poison.py")
                poison_fn(pagep)
                raise SystemExit("反向验证失败：污染 reqUrl 后生成链居然通过")
            except AssertionError as e:
                msg = str(e)
                assert "请求数为 0" in msg, f"污染口径失败形态不对: {msg[:200]}"
                print(f"pytest chain poisoned reqUrl: FAIL as expected → {msg[:120]}")
            execp = replay_trace(pagep, poisoned, navigate=False,
                                 timeout_ms=8000)
            assert execp["status"] != "success", "污染 reqUrl 后 trace 链居然通过"
            print("trace chain poisoned reqUrl: FAIL as expected")
            ctxp.close()
            poison_srv.shutdown()
            browser.close()
        print("ALL GATES PASS")
    finally:
        srv.shutdown()
        quiet_srv.shutdown()


def _retarget(steps, origin, req_url_map=None):
    """把录制步骤的 pageOrigin/reqUrl 重定向到新 origin（回放环境换了端口）。

    req_url_map: {旧 url 片段: 新绝对 url} —— 缺省把 /api/save 映到同路径。
    """
    out = json.loads(json.dumps(steps))
    for s in out:
        if s.get("pageOrigin"):
            s["pageOrigin"] = origin
        if s.get("reqUrl") is not None:
            new = (req_url_map or {}).get("/api/save")
            s["reqUrl"] = new if new is not None else origin + "/api/save"
    return out


if __name__ == "__main__":
    main()
