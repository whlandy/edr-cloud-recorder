"""真实 headed 录制 + 双回放 + 正反验证：网络范围断言（scope=network）端到端。

流程（record.py record_session 真实录制，headed Chrome）：
  1. 用户（此处 on_ready 驱动）点按钮触发 POST /api/save → 201
  2. 右键打开断言菜单 → 范围切「最近请求」→ 选 POST /api/save →
     断言类型「响应状态码」→ expected=201 → 保存
  3. 产物（recording.json / trace.json / test_*.py）原子落盘
  4. 生成链（pytest）回放：应通过
  5. 回放链（replay_trace）：应通过
  6. 篡改 expected（201→500）重新生成：两条链都必须明确失败
  7. 失败信息必须包含具体断言/实际值/期望值

产物在 recordings/p2-network-scope-recording/ 下，与提交绑定。
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
from replay_trace import replay_trace  # noqa: E402
from record import record_session  # noqa: E402

CASE = "p2-network-scope-recording"
OUT = Path(__file__).resolve().parents[1] / "recordings" / CASE

HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>网络断言录制页</title></head><body>
<button id="save" onclick="fetch('/api/save',{method:'POST',
  body:JSON.stringify({order:'A-1'})}).then(r=>r.json()).then(d=>{
  document.getElementById('out').textContent = '已保存 ' + d.id;
})">保存订单</button>
<div id="out" style="margin-top:16px"></div>
</body></html>"""


def _serve():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            import json as _json
            body = _json.dumps({"ok": True, "id": "S-2049"}).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def drive(page, pump):
    """on_ready 回调：真实驱动一遍「点击 → 右键 → 菜单选网络断言」。"""
    page.bring_to_front()
    # 1) 业务点击：触发 POST /api/save
    page.click("#save")
    page.wait_for_timeout(600)
    assert "已保存" in page.inner_text("#out"), "业务点击没有生效"
    pump()

    # 2) 右键打开断言菜单（菜单挂在 body 上；右键 #out 附近空白处也行，
    #    但 afterStepId 会指向最后一步 —— 这里右键 #out 让因果链清晰）
    box = page.locator("#out").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2,
                     button="right")
    menu = page.frame_locator("#__rec_assert_menu__")
    menu.locator("#t").wait_for(state="visible")
    # 范围 → 最近请求
    menu.locator("#sc").select_option("network")
    # 请求清单来自宿主 __recNet（expose_binding 异步回填）；等到非空为止
    page.wait_for_function(
        """() => {
          const f = document.getElementById('__rec_assert_menu__');
          if (!f) return false;
          const sel = f.contentDocument.getElementById('netReq');
          return !!sel && sel.options.length > 0 && sel.options[0].value !== '';
        }""", timeout=5000)
    values = [menu.locator("#netReq option").nth(i).get_attribute("value")
              for i in range(menu.locator("#netReq option").count())]
    target = next(v for v in values if "POST" in v and "/api/save" in v)
    menu.locator("#netReq").select_option(value=target)
    # 类型 → 响应状态码；expected=201
    menu.locator("#t").select_option("status")
    menu.locator("#es").fill("201")
    menu.get_by_role("button", name="添加断言").click()
    page.wait_for_timeout(400)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    srv, origin = _serve()
    result = record_session(
        start_url=origin + "/", name=CASE,
        out_dir=str(OUT.parent), state_dir="/tmp/rec-p2-net-state",
        on_ready=drive,
    )
    print("record ok:", result["raw_file"])

    recording = json.loads(Path(result["raw_file"]).read_text())
    steps = recording["steps"]
    net_asserts = [s for s in steps
                   if s.get("type") == "assert" and s.get("scope") == "network"]
    assert net_asserts, "录制没有产生 scope=network 断言步骤"
    print("recorded network assert:", net_asserts[0])

    # 正向：生成链（exec pytest 函数）
    spec_text = Path(result["spec_file"]).read_text()
    trace = json.loads(Path(result["trace_file"]).read_text())
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
                                     executable_path=resolve_chrome())
        ctx = browser.new_context(base_url=origin + "/")

        # 生成链正向
        page = ctx.new_page()
        ns = {"__file__": str(OUT / f"test_{CASE}.py")}
        exec(compile(spec_text, f"{CASE}.py", "exec"), ns)
        test_fn = next(v for k, v in ns.items()
                       if k.startswith("test_") and callable(v))
        test_fn(page)
        print("pytest chain positive: PASS")

        # 回放链正向
        exec2 = replay_trace(page, trace, navigate=False, timeout_ms=5000)
        assert exec2["status"] == "success", exec2["steps"]
        print("trace chain positive: PASS")

        # 反向：篡改 expected 201 → 500，重新生成两条链，都必须明确失败
        steps2 = json.loads(json.dumps(steps))
        for s in steps2:
            if s.get("type") == "assert" and s.get("scope") == "network":
                s["expected"] = 500
        spec_bad = generate_spec(steps2, recording["net"],
                                 start_url=origin + "/", name=CASE)
        from generate_trace import generate_trace
        trace_bad = generate_trace(steps2, recording["net"], name=CASE,
                                   start_url=origin + "/")
        page2 = ctx.new_page()
        ns2 = {"__file__": str(OUT / f"test_{CASE}.py")}
        exec(compile(spec_bad, "bad.py", "exec"), ns2)
        test_bad = next(v for k, v in ns2.items()
                        if k.startswith("test_") and callable(v))
        try:
            test_bad(page2)
            raise SystemExit("反向验证失败：篡改 expected 后生成链居然通过")
        except AssertionError as e:
            msg = str(e)
            assert "500" in msg and "201" in msg, \
                f"失败信息没有同时给出实际值和期望值: {msg}"
            print(f"pytest chain tampered: FAIL as expected → {msg[:160]}")
        exec3 = replay_trace(page2, trace_bad, navigate=False, timeout_ms=5000)
        assert exec3["status"] != "success", "篡改后 trace 链居然通过"
        errs = [s.get("error", "") for s in exec3["steps"] if s.get("error")]
        joined = " | ".join(errs)
        assert "500" in joined and "201" in joined, \
            f"trace 失败信息没有同时给出实际值和期望值: {joined}"
        print(f"trace chain tampered: FAIL as expected → {joined[:160]}")
        browser.close()
    print("ALL GATES PASS")


if __name__ == "__main__":
    main()
