"""Windows 适配批次 B：无凭据本地 fixture 冒烟。

目标：在真实 Windows CI（或可控 Windows 主机）上给出「录制 → 生成 spec/trace →
本地 fixture 回放 → .auth 写盘与恢复」的最小闭环证据；本机（macOS/Linux）同样
可运行——两平台都只依赖 Playwright Chromium 与本地 127.0.0.1 HTTP fixture。

硬约束：
- 完全无真实凭据：不登录、不访问真实业务系统、不做真实业务写入；
  登录态为本地自造 cookie/localStorage，仅用于验证 storage_state 写盘与恢复机制。
- 不改变凭据安全模型、trace schema、断言、录制、生成或回放语义；
  不新增 Windows 原生依赖（只读复用 assets/rec_session.restore_context_session）。
- 在真实 Windows 执行前，本文件通过只证明「Windows 代码级兼容 / 批次 B 准备完成」，
  不得作为「Windows 已验收」。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "assets"))

from generate_spec import generate_spec  # noqa: E402
from generate_trace import generate_trace  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from replay_trace import replay_trace  # noqa: E402
import rec_session  # noqa: E402  批次 A 改动文件：只读复用，不修改

from fixture_drive import RECORDER_MJS, _serve  # noqa: E402
from recorder_loader import recorder_source  # noqa: E402

# 极简本地 fixture 页面（127.0.0.1 专用，无凭据、无外部地址）。
SMOKE_HTML = """<!doctype html><meta charset="utf-8"><body>
  <input placeholder="冒烟输入">
  <button data-testid="smoke-send" onclick="sendOrder()">提交</button>
  <script>function sendOrder(){fetch('/api/ok',{method:'POST',body:JSON.stringify({a:1})})}</script>
</body>"""


def _generate_smoke_artifacts(tmp_path: Path) -> tuple[str, dict, str, object]:
    """最小录制→生成闭环：用产品录制器在本地 fixture 页面上真实录制，
    生成 spec（Python 回放脚本）与 trace（dict）。返回 (spec, trace, base, server)。
    录制协议与 fixture_drive.drive 一致（__recPush 双通道 + 升级按 id 覆盖）。"""
    steps: list[dict] = []
    seen: set[str] = set()
    net: list[dict] = []

    def accept(st):
        if not st or not st.get("id"):
            return
        if st.get("_upgrade"):
            for old in steps:
                if old["id"] == st["id"]:
                    allowed = st.get("_only") or (
                        "type", "to", "via", "sel", "kind", "ambiguous",
                        "matches", "label", "css", "dismissesOverlay")
                    old.update({k: v for k, v in st.items() if k in allowed})
                    return
            return
        if st["id"] not in seen:
            seen.add(st["id"])
            # 与 fixture_drive.drive 相同：模板是「事后截图」的占位标记，
            # generate_trace 据此把 click 步骤标记为 ready（视觉回退只在
            # DOM 定位失败时才需要真实模板文件）。
            if st.get("type") in ("click", "dblclick", "switch", "check", "uncheck"):
                st.setdefault("ui", {})["templates"] = {"element": f"{st['id']}.png"}
            steps.append(st)

    srv = _serve({"/": SMOKE_HTML})
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            context.expose_binding("__recPush", lambda source, st: accept(st))
            context.add_init_script(script=recorder_source(RECORDER_MJS))
            page = context.new_page()
            page.goto(base)
            page.fill('input[placeholder="冒烟输入"]', "smoke-value")
            page.click("[data-testid=smoke-send]")
            page.wait_for_timeout(400)  # 给 change/request 上报留出时间
            browser.close()
        spec = generate_spec(steps, net, start_url=base, name="windows-smoke")
        trace = generate_trace(steps, net, name="windows-smoke", start_url=base)
    except Exception:
        srv.shutdown()
        raise
    assert steps, "本地 fixture 录制必须产生步骤"
    return spec, (trace if isinstance(trace, dict) else json.loads(trace)), base, srv

FIXTURE_ORIGIN_REPL = None  # 生成产物含 127.0.0.1 随机端口，属预期


def _facts() -> str:
    """记录版本与路径事实（计划要求：记录 Python/Playwright/Chrome 版本与路径）。"""
    import platform

    from importlib.metadata import version as _pkg_version

    with sync_playwright() as p:
        chrome = p.chromium.executable_path
    return (
        f"python={platform.python_version()} platform={platform.platform()}\n"
        f"playwright={_pkg_version('playwright')}\n"
        f"chromium={chrome}"
    )


def test_smoke_environment_facts(capsys):
    print(_facts())
    out = capsys.readouterr().out
    assert "python=" in out and "chromium=" in out


def test_smoke_record_generate_spec_and_trace(recording, tmp_path):
    """本地 fixture 录制 → 生成 spec/trace → 写盘为合法 JSON（无凭据、无真实系统）。"""
    spec = generate_spec(recording["steps"], recording["net"],
                         start_url=recording["startUrl"], name="windows-smoke")
    trace = generate_trace(recording["steps"], recording["net"],
                           name="windows-smoke", start_url=recording["startUrl"])
    spec_path = tmp_path / "windows-smoke.spec.json"
    trace_path = tmp_path / "windows-smoke.trace.json"
    spec_path.write_text(spec, encoding="utf-8")
    trace_path.write_text(
        trace if isinstance(trace, str) else json.dumps(trace, ensure_ascii=False),
        encoding="utf-8")
    assert spec_path.stat().st_size > 0 and trace_path.stat().st_size > 0
    spec_text = spec_path.read_text(encoding="utf-8")
    # generate_spec 产物是可直接执行的 Python 回放脚本
    assert "def " in spec_text and "Page" in spec_text, "spec 必须是回放脚本"
    # 最小关键断言：本地 fixture 的 data-testid 保存按钮进入选择器
    assert "save-btn" in spec_text, "spec 必须包含 data-testid 保存按钮的选择器"


def test_smoke_auth_state_write_and_restore(tmp_path):
    """.auth 写盘与恢复的最小等价闭环（自造本地登录态，无凭据）：
    写盘 = context.storage_state(path=...)（与 assets/auth_setup.py 同一调用）；
    恢复 = assets/rec_session.restore_context_session（产品恢复侧路径），
    以本地 fixture 页面回读注入值验证生效。"""
    from fixture_drive import _serve, _pages

    auth_dir = tmp_path / ".auth"
    auth_dir.mkdir(parents=True, exist_ok=True)  # 与产品 auth_dir 构建一致
    state_path = auth_dir / "state.json"
    marker = "smoke-local-only"
    origin_seen = {}

    srv = _serve(_pages())
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            # 1) 自造本地登录态并写盘
            ctx1 = browser.new_context()
            page1 = ctx1.new_page()
            page1.goto(base)
            page1.evaluate("v => localStorage.setItem('smoke_key', v)", marker)
            ctx1.add_cookies([{
                "name": "smoke_cookie", "value": marker, "url": base,
            }])
            ctx1.storage_state(path=str(state_path))  # 与 auth_setup.py 落盘同调用
            assert state_path.stat().st_size > 0, "storage_state 必须写盘"
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            assert saved.get("origins"), "state.json 必须包含 origins"

            # 2) 新 context 恢复（产品恢复侧函数），本地页面回读验证
            ctx2 = browser.new_context()
            assert rec_session.restore_context_session(ctx2, auth_dir) is True
            page2 = ctx2.new_page()
            page2.goto(base)
            origin_seen["local_storage"] = page2.evaluate(
                "localStorage.getItem('smoke_key')")
            origin_seen["cookie"] = next(
                (c["value"] for c in ctx2.cookies(base)
                 if c["name"] == "smoke_cookie"), None)
            browser.close()
    finally:
        srv.shutdown()
    assert origin_seen["local_storage"] == marker
    assert origin_seen["cookie"] == marker


def test_smoke_generated_spec_actually_executes(tmp_path):
    """缺口 1 回归：生成的 spec 必须在全新 Chromium page/context 上真正执行成功。

    输入为极简本地 fixture 页面 + 产品录制器（recorder_source）真实录制的步骤
    （与 fixture_drive 同协议），全部限定 127.0.0.1，无凭据、无外部主机。
    """
    spec_text, _trace, base, srv = _generate_smoke_artifacts(tmp_path)
    spec_file = tmp_path / "windows-smoke.spec.py"
    spec_file.write_text(spec_text, encoding="utf-8")
    namespace = {"__file__": str(spec_file), "__name__": "windows_smoke_spec"}
    exec(compile(spec_text, str(spec_file), "exec"), namespace)
    try:  # 成功/失败路径都必须清理 browser 与本地 HTTP server
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_context(base_url=base).new_page()
                executed = [fn for name, fn in namespace.items()
                            if name.startswith("test_") and callable(fn)]
                assert executed, "生成 spec 必须包含可执行测试函数"
                executed[0](page)  # 真实执行；任何步骤失败都会抛异常
            finally:
                browser.close()  # 须在 sync_playwright 事件循环存活时关闭
    finally:
        srv.shutdown()


def test_smoke_replay_trace_consumes_generated_trace(tmp_path):
    """缺口 1 回归：generate_trace 产物必须真正交给 replay_trace 回放成功。"""
    _spec_text, trace, base, srv = _generate_smoke_artifacts(tmp_path)
    try:  # replay_trace 抛异常时同样必须清理 browser 与 server
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_context(base_url=base).new_page()
                execution = replay_trace(page, trace, template_root=tmp_path,
                                         timeout_ms=5000)
            finally:
                browser.close()  # 须在 sync_playwright 事件循环存活时关闭
    finally:
        srv.shutdown()
    assert execution.get("status") == "success", execution.get("steps")
    assert execution.get("steps"), "回放必须产生步骤记录"


def test_smoke_no_real_credentials_or_hosts():
    """守卫：冒烟资源仅允许本地回环 fixture——任何外部/真实系统地址都算失败。"""
    from fixture_drive import _pages

    html = "\n".join(_pages().values())
    for banned in ("https://", "corp.example", "login.example", "edr.", "10.", "REDACTED-INTERNAL-IP-"):
        assert banned not in html, f"fixture 页面不得包含 {banned}"
