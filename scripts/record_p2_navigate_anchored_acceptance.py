"""真实 headed 录制 + 双回放 + 正反验证：count=0 锚在 navigate 步骤（P2.12）。

场景（quality-log P2.11 剩余风险 #2 生成门不对称，2026-10-31 本轮）：

  1. 真实 headed Chrome 录制：点 #save 真发 POST /api/save（菜单里才有
     可绑定请求）→ page.reload()（产出 navigate/reload 步骤 =「最近一步」）
     → 右键菜单：范围=最近请求 → 类型=不应发请求 → 绑定 POST → 保存。
     afterStepId 由菜单绑到「最近一步」= reload 步骤（rc-1 锚在 nav 上，
     修复前 generate_spec 拒发整份 spec / generate_trace 接受 —— 不对称）。
  2. record.py 生成管线原子落盘 recording.json / trace.json / test_*.py。
  3. 生成链（pytest 函数真实执行）：观察者赶在 reload 前创建，reload 后
     走满稳定窗 → 正向通过。
  4. 回放链（replay_trace）：Navigate 节点预扫建观察者 → 正向通过。
  5. 反向 1：重放环境换成「页面每次加载（含 reload 后新文档）就发 POST」
     —— 双链都必须失败且指回断言（请求数 actual=1）。
  6. 反向 2：篡改录制产物 reqUrl 口径（指向 fire 环境真实存在的
     GET /api/ping）→ 双链都必须失败（actual=1）—— 防测试空转。
  7. 反向 3：禁用修复（emit_action 的 network_asserts 传 None）重新生成
     spec → SpecGenerationError 复现 —— 证明真实录制产物确实暴露了被修复
     的生成门不对称缺陷。

产物在 recordings/p2-navigate-anchored-recording/ 下；recordings/ 按仓库
策略 gitignore，由本脚本复现。
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
import trace_schema as ts  # noqa: E402
from record import record_session  # noqa: E402
from replay_trace import replay_trace  # noqa: E402

CASE = "p2-navigate-anchored-recording"
OUT = Path(__file__).resolve().parents[1] / "recordings" / CASE


def _serve(*, on_load_post: bool):
    """on_load_post=True：页面每次加载（含 reload 的新文档）发
    POST /api/save + GET /api/ping —— 反向环境的负断言触发器：反向 1 靠
    save，反向 2 的污染口径靠 ping。setTimeout 定时器会被 reload 抹掉，
    不能当触发器（单测实测假绿）。正向环境页面完全静默。"""
    load_js = ("<script>fetch('/api/save',{method:'POST',body:'x=1'})"
               "</script><script>fetch('/api/ping')</script>"
               if on_load_post else "")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/api/ping":
                ping = b'{"pong": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(ping)))
                self.end_headers()
                self.wfile.write(ping)
                return
            body = (f"<!doctype html><html><head><meta charset='utf-8'>"
                    f"<title>navz</title></head><body>{load_js}"
                    f"<button id='save' onclick=\"void 0\">save</button>"
                    f"<div id='marker'>x</div></body></html>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
    """on_ready 回调：真实节奏录下「锚在 reload 上的 count=0 断言」。

    1) 点 #save：页面静态（onclick=void 0），点了不发请求 —— 但要让菜单
       的最近请求清单里有可绑定请求（菜单红线：没有请求就禁保存），所以
       先用 page.evaluate 真发一条 POST /api/save；
    2) page.reload()：产出 navigate/reload 步骤（= 菜单的「最近一步」）；
    3) 右键菜单：最近请求范围 → count=0 → 绑定 POST → 保存。
    """
    page.bring_to_front()
    pump()
    # 1) 真实写请求（可绑定；页面按钮本身不发请求）
    page.evaluate("fetch('/api/save',{method:'POST',body:'x=1'})")
    page.wait_for_timeout(700)              # POST 完成、响应进最近清单
    pump()
    # 2) 重载：navigate/reload 步骤落地（输入框无内容可清）
    page.reload(wait_until="load")
    page.wait_for_timeout(400)
    pump()
    # 3) 菜单：最近请求范围 + count=0，绑定 POST /api/save
    box = page.locator("#marker").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2,
                     box["y"] + box["height"] / 2, button="right")
    menu = page.frame_locator("#__rec_assert_menu__")
    menu.locator("#sc").wait_for(state="visible")
    menu.locator("#sc").select_option("network")
    menu.locator("#t").select_option("request_count")
    page.wait_for_function(
        """() => {
          const f = document.querySelector('#__rec_assert_menu__');
          const doc = f.contentDocument || f.contentWindow.document;
          const sel = doc.getElementById('netReq');
          return !!sel && [...sel.options].some(o => o.value);
        }""", timeout=10_000)
    options = menu.locator("#netReq option")
    picked = None
    for i in range(options.count()):
        value = options.nth(i).get_attribute("value") or ""
        if '"POST"' in value and picked is None:
            picked = value
    assert picked, "最近请求清单里没有 POST 请求可绑定"
    menu.locator("#netReq").select_option(picked)
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


def _retarget(steps, origin):
    out = json.loads(json.dumps(steps))
    for s in out:
        if s.get("pageOrigin"):
            s["pageOrigin"] = origin
        if s.get("reqUrl") is not None:
            s["reqUrl"] = origin + "/api/save"
    return out


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    srv, origin = _serve(on_load_post=False)
    fire_srv, fire_origin = _serve(on_load_post=True)
    try:
        # ── 1) 真实 headed 录制 ──
        result = record_session(
            start_url=origin + "/", name=CASE,
            out_dir=str(OUT.parent), state_dir="/tmp/rec-p2-navz-state",
            on_ready=drive,
        )
        print("record ok:", result["raw_file"])

        recording = json.loads(Path(result["raw_file"]).read_text())
        steps = recording["steps"]
        print("recorded steps:",
              [(s.get("type"), (s.get("assertion") or s.get("navType") or ""),
                s.get("url")) for s in steps])
        # 录制产物核验：navigate/reload 步骤存在，count=0 断言锚在它上面
        navs = [s for s in steps if s.get("type") == "navigate"
                and s.get("navType") == "reload"]
        assert navs, f"录制缺 navigate/reload 步骤：{[s.get('type') for s in steps]}"
        nav = navs[0]
        rc = [s for s in steps if s.get("type") == "assert"
              and s.get("scope") == "network"
              and s.get("assertion") == "request_count"]
        assert rc, f"count=0 断言未被录制：{[s.get('type') for s in steps]}"
        rcs = rc[0]
        assert rcs.get("expected") == 0, rcs
        assert rcs.get("reqMethod") == "POST" and "/api/save" in rcs.get("reqUrl", "")
        assert rcs.get("afterStepId") == nav["id"], (
            f"count=0 没锚在 reload 步骤上: {rcs.get('afterStepId')} != {nav['id']}")
        print("recorded assertion ok:", rcs["id"], "after", nav["id"], "(reload)")

        spec_text = Path(result["spec_file"]).read_text()
        trace = json.loads(Path(result["trace_file"]).read_text())
        # 录制产物核验 2：三产物断言一致性（record.py 已自检，这里再验）
        spec_ids = [ln.split()[-1] for ln in spec_text.splitlines()
                    if ln.strip().startswith("# rec-assert:")]
        rec_ids = [s["id"] for s in steps if s.get("type") == "assert" and s.get("id")]
        tr_ids = [ts.provenance(n).get("sourceStepId")
                  for _, n in ts.nodes(trace) if ts.assertion_of(n)]
        assert spec_ids == rec_ids == tr_ids, (spec_ids, rec_ids, tr_ids)
        assert ts.meta(trace).get("status") == "ready", ts.meta(trace).get("status")
        print("artifact consistency ok:", spec_ids)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=resolve_chrome())

            # ── 正向 1：生成链（观察者赶在 reload 前，reload 后走满稳定窗）──
            ctx = browser.new_context(base_url=origin + "/")
            page = ctx.new_page()
            test_fn = _run_spec(spec_text, page, f"{CASE}.py")
            test_fn(page)
            print("pytest chain positive: PASS")

            # ── 正向 2：回放链（Navigate 节点预扫建观察者）──
            page2 = ctx.new_page()
            page2.goto(origin + "/")
            exec2 = replay_trace(page2, trace, navigate=False, timeout_ms=8000)
            assert exec2["status"] == "success", exec2["steps"]
            print("trace chain positive: PASS")
            ctx.close()

            # ── 反向 1：换 fire 环境（reload 后新文档自己发 POST）→ 双链失败 ──
            from generate_spec import generate_spec  # noqa: E402
            from generate_trace import generate_trace  # noqa: E402
            bad_steps = _retarget(steps, fire_origin)
            bad_spec = generate_spec(bad_steps, [], start_url=fire_origin + "/",
                                     name=CASE)
            bad_trace = generate_trace(bad_steps, [], name=CASE,
                                       start_url=fire_origin + "/")
            ctxb = browser.new_context(base_url=fire_origin + "/")
            pageb = ctxb.new_page()
            pageb.goto(fire_origin + "/")
            bad_fn = _run_spec(bad_spec, pageb, "bad.py")
            try:
                bad_fn(pageb)
                raise SystemExit("反向验证失败：fire 环境生成链居然通过")
            except AssertionError as e:
                msg = str(e)
                assert "请求数为 0" in msg and "actual=1" in msg, (
                    f"生成链失败信息形态不对: {msg[:200]}")
                print(f"pytest chain negative (on-load POST): FAIL as expected "
                      f"→ {msg[:120]}")
            pageb2 = ctxb.new_page()
            pageb2.goto(fire_origin + "/")
            execb = replay_trace(pageb2, bad_trace, navigate=False,
                                 timeout_ms=8000)
            assert execb["status"] != "success", "fire 环境 trace 链居然通过"
            print("trace chain negative (on-load POST): FAIL as expected")
            ctxb.close()

            # ── 反向 2：篡改录制产物 reqUrl 口径（指向真实存在的 /api/ping，
            #    fire 环境 reload 会真的发出 GET）→ 双链失败，防测试空转 ──
            steps3 = _retarget(json.loads(json.dumps(steps)), fire_origin)
            for s in steps3:
                if (s.get("type") == "assert"
                        and s.get("assertion") == "request_count"):
                    s["reqUrl"] = fire_origin + "/api/ping"
                    s["reqMethod"] = "GET"
            poison_spec = generate_spec(steps3, [], start_url=fire_origin + "/",
                                        name=CASE)
            poison_trace = generate_trace(steps3, [], name=CASE,
                                          start_url=fire_origin + "/")
            ctxp = browser.new_context(base_url=fire_origin + "/")
            pagep = ctxp.new_page()
            pagep.goto(fire_origin + "/")
            poison_fn = _run_spec(poison_spec, pagep, "poison.py")
            try:
                poison_fn(pagep)
                raise SystemExit("反向验证失败：污染口径后生成链居然通过")
            except AssertionError as e:
                msg = str(e)
                assert "请求数为 0" in msg, f"污染口径失败形态不对: {msg[:200]}"
                print(f"pytest chain poisoned reqUrl: FAIL as expected → {msg[:120]}")
            execp = replay_trace(ctxp.new_page(), poison_trace,
                                 navigate=False, timeout_ms=8000)
            ctxp.close()
            assert execp["status"] != "success", "污染口径后 trace 链居然通过"
            print("trace chain poisoned reqUrl: FAIL as expected")

            # ── 反向 3：禁用修复重新生成 spec → SpecGenerationError 复现 ──
            # 证明真实录制产物确实暴露了被修复的生成门不对称缺陷。
            import generate_spec as gs  # noqa: E402
            saved_emit = gs.generate_spec.__module__ and None
            # 直接复刻修复前的行为：emit_action 拿不到 navigate 名下的断言
            import types
            src = Path(gs.__file__).read_text()
            old = ("network_asserts=_network_by_action.pop(s.get(\"id\"), None)))\n"
                   "            if s.get(\"id\"):\n"
                   "                lines.append(f\"    # rec-step: {s['id']}\")\n"
                   "            continue\n\n"
                   "        # ── 页面关闭")
            assert old in src, "generate_spec navigate 修复代码形态变了，反向脚本需更新"
            mutated = src.replace(
                "network_asserts=_network_by_action.pop(s.get(\"id\"), None)))\n"
                "            if s.get(\"id\"):\n"
                "                lines.append(f\"    # rec-step: {s['id']}\")\n"
                "            continue\n\n"
                "        # ── 页面关闭",
                "network_asserts=None))\n"
                "            if s.get(\"id\"):\n"
                "                lines.append(f\"    # rec-step: {s['id']}\")\n"
                "            continue\n\n"
                "        # ── 页面关闭", 1)
            mod = types.ModuleType("gs_disabled")
            mod.__dict__["__name__"] = "gs_disabled"
            import tempfile
            tmp = Path(tempfile.mkdtemp()) / "generate_spec_disabled.py"
            tmp.write_text(mutated)
            sys.path.insert(0, str(tmp.parent))
            try:
                import importlib.util
                spec_mod = importlib.util.spec_from_file_location(
                    "gs_disabled", tmp)
                mod = importlib.util.module_from_spec(spec_mod)
                spec_mod.loader.exec_module(mod)
                try:
                    mod.generate_spec(steps, [], start_url=origin + "/",
                                      name=CASE)
                    raise SystemExit(
                        "反向验证失败：禁用修复后 generate_spec 居然接受真实录制")
                except mod.SpecGenerationError as e:
                    assert "nav" in str(e) or "不在生成后的步骤序列" in str(e)
                    print("fix-disabled spec regeneration: REJECTED as expected "
                          f"→ {str(e).strip().splitlines()[-1][:110]}")
            finally:
                sys.path.remove(str(tmp.parent))
                shutil.rmtree(tmp.parent, ignore_errors=True)

            browser.close()
        print("ALL GATES PASS")
    finally:
        srv.shutdown()
        fire_srv.shutdown()


if __name__ == "__main__":
    main()
