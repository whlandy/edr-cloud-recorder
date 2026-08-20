import json
from pathlib import Path

import pytest

from generate_spec import generate_spec, prepare_steps
from generate_trace import generate_trace
import trace_schema as ts
from rec_assert import ANY_NUM
from rec_config import load_config
from record import (
    _artifact_paths,
    _capture_storage,
    _capture_ui_template,
    _crop_pre_frame,
    _now,
    _request_body_fields,
    _select_pre_frame,
    _visible_clip,
)
from selector_py import to_python
from rec_secrets import REDACTED, redact_text


def _solid_png(width: int, height: int) -> bytes:
    import cv2
    import numpy as np

    ok, encoded = cv2.imencode(
        ".png", np.full((height, width, 3), 240, dtype=np.uint8)
    )
    assert ok
    return encoded.tobytes()


def test_artifact_paths_keep_one_case_in_one_directory(tmp_path):
    paths = _artifact_paths(tmp_path / "recordings", "login-flow")

    assert paths == {
        "case_dir": (tmp_path / "recordings/login-flow").resolve(),
        "asset_dir": (tmp_path / "recordings/login-flow/assets").resolve(),
        "raw_file": (tmp_path / "recordings/login-flow/recording.json").resolve(),
        "trace_file": (tmp_path / "recordings/login-flow/trace.json").resolve(),
        "spec_file": (tmp_path / "recordings/login-flow/test_login_flow.py").resolve(),
    }


@pytest.mark.parametrize(
    "name", ["", "../escape", "nested/case", r"nested\case", ".", ".."]
)
def test_artifact_paths_reject_path_traversal(tmp_path, name):
    with pytest.raises(ValueError, match="单个目录名"):
        _artifact_paths(tmp_path, name)


def test_any_num_rejects_bool():
    assert not (ANY_NUM == True)
    assert ANY_NUM == 42


def test_selector_translates_alt_text():
    assert to_python('getByAltText("Logo")') == 'get_by_alt_text("Logo")'


def test_load_config_prefers_module_dir(monkeypatch, tmp_path):
    module_dir = tmp_path / "module"
    cwd = tmp_path / "cwd"
    module_dir.mkdir()
    cwd.mkdir()
    (module_dir / "config.json").write_text('{"baseUrl": "https://module.example"}', encoding="utf-8")
    (cwd / "config.json").write_text('{"baseUrl": "https://cwd.example"}', encoding="utf-8")

    monkeypatch.setattr("rec_config.HERE", module_dir)
    monkeypatch.chdir(cwd)

    cfg = load_config(None)
    assert cfg["baseUrl"] == "https://module.example"


def test_generate_spec_rejects_origin_mismatch():
    steps = [
        {"t": 100, "type": "click", "kind": "text",
         "sel": 'getByText("Save", { exact: true })'},
    ]
    net = [
        {"id": 1, "t": 110, "phase": "req", "method": "POST",
         "url": "http://localhost:56964/api/ok", "body": "{}"},
        {"requestId": 1, "t": 120, "phase": "res", "method": "POST",
         "url": "http://localhost:56964/api/ok", "status": 200},
    ]

    with pytest.raises(ValueError, match="origin"):
        generate_spec(steps, net, start_url="http://localhost/start", name="mismatch")


def test_generate_spec_adds_visual_fallback_without_style_noise():
    steps = [{
        "t": 100,
        "type": "click",
        "kind": "role",
        "sel": 'getByRole("button", { name: "Save", exact: true })',
        "ui": {
            "pageRect": {"x": 10, "y": 20, "width": 80, "height": 32},
            "click": {"rx": 0.25, "ry": 0.5},
            "style": {"color": "red"},
            "templates": {
                "element": {
                    "path": "flow.assets/step-0001.element.png",
                    "width": 80,
                    "height": 32,
                },
            },
        },
    }]

    spec = generate_spec(
        steps, [], start_url="http://localhost/start", name="flow"
    )

    assert "visual_click(page, page.get_by_role" in spec
    assert "flow.assets/step-0001.element.png" in spec
    assert "'style'" not in spec


def test_generated_secret_input_requires_environment_variable():
    steps = [
        {"t": 100, "type": "click", "kind": "text",
         "sel": 'getByText("Settings", { exact: true })'},
        {"t": 110, "type": "fill", "kind": "placeholder",
         "sel": 'getByPlaceholder("New password")', "secret": True},
    ]

    spec = generate_spec(
        steps, [], start_url="https://app.example/settings", name="secret"
    )

    assert 'os.environ["REC_PASSWORD"]' in spec
    assert 'os.environ.get("REC_PASSWORD", "")' not in spec


def test_top_level_login_form_is_removed_through_submit_button():
    steps = [
        {"t": 100, "type": "click", "sel": 'getByPlaceholder("用户名")'},
        {"t": 110, "type": "fill", "sel": 'getByPlaceholder("用户名")',
         "value": "alice"},
        {"t": 120, "type": "fill", "sel": 'getByPlaceholder("密码")',
         "secret": True},
        {"t": 130, "type": "click", "label": "登录",
         "sel": 'getByRole("button", { name: "登录", exact: true })'},
        {"t": 140, "type": "click", "label": "7天",
         "sel": 'getByRole("button", { name: "7天", exact: true })'},
    ]

    dropped, prepared = prepare_steps(steps)

    assert len(dropped) == 4
    assert [step.get("label") for step in prepared] == ["7天"]


def test_generate_trace_creates_one_trace_with_template_click_steps():
    input_templates = {
        "element": {"path": "flow.assets/step-0001.element.png"},
        "context": {"path": "flow.assets/step-0001.context.png"},
    }
    steps = [
        {
            "id": "focus-input",
            "t": 100,
            "type": "click",
            "kind": "placeholder",
            "sel": 'getByPlaceholder("Username")',
            "css": "#username",
            "ui": {
                "pageRect": {"x": 10, "y": 20, "width": 80, "height": 32},
                "pageViewport": {"width": 1440, "height": 900},
                "click": {"rx": 0.25, "ry": 0.75},
                "templates": input_templates,
            },
        },
        {
            "id": "input", "t": 110, "type": "fill", "value": "alice",
            "sel": 'getByPlaceholder("Username")', "css": "#username",
        },
        {
            "id": "check", "t": 120, "type": "check", "sel": 'locator("#agree")',
            "ui": {
                "pageRect": {"x": 10, "y": 60, "width": 16, "height": 16},
                "templates": {
                    "element": {"path": "flow.assets/step-0003.element.png"},
                },
            },
        },
        {
            "id": "assert", "t": 130, "type": "assert", "assertion": "visible",
            "expected": True, "sel": 'getByText("Saved")',
        },
        {
            "id": "missing-click",
            "t": 140,
            "type": "click",
            "kind": "css",
            "sel": 'locator("#confirm")',
            "ui": {"click": {"rx": 0.5, "ry": 0.5}},
        },
    ]

    trace = generate_trace(steps, name="flow", start_url="https://app.example")

    meta = ts.meta(trace)
    assert meta["schema"] == ts.SCHEMA
    assert meta["entry"] == "step_0001"
    assert [ts.provenance(node)["sourceStepId"] for _, node in ts.nodes(trace)] == [
        "input", "check", "assert", "missing-click",
    ]
    fill, check, assertion, missing = (node for _, node in ts.nodes(trace))
    assert fill["recognition"]["type"] == "TemplateMatch"
    # recognition.param.template 只放一个（maa-fw 的形状），完整回退顺序在 provenance
    assert fill["recognition"]["param"]["template"] == input_templates["context"]
    assert ts.template_order(fill) == ["context", "element"]
    assert ts.templates_of(fill) == input_templates
    assert ts.provenance(fill)["sourceStepIds"] == ["focus-input", "input"]
    assert fill["action"] == {
        "type": "InputText",
        "param": {"text": "alice", "focusBeforeInput": True},
    }
    assert ts.node_status(fill) == "ready"
    assert fill["next"] == ["step_0002"]
    assert check["recognition"]["type"] == "TemplateMatch"
    assert check["action"]["type"] == "Check"
    # 断言不再是一种动作类型 —— 动作是 DoNothing，规格在 attach.verification
    assert assertion["action"]["type"] == "DoNothing"
    assert ts.assertion_of(assertion)["assertion"] == "visible"
    assert ts.node_status(missing) == "missing_template"
    assert missing["next"] == []
    assert meta["status"] == "incomplete"


def test_double_click_is_one_visual_step_in_script_and_trace():
    ui = {
        "pageRect": {"width": 40, "height": 20},
        "templates": {
            "element": {
                "path": "flow.assets/step-0002.element.png",
                "width": 40,
                "height": 20,
            },
        },
    }
    steps = [
        {"id": "first-click", "t": 100, "type": "click", "sel": 'locator("#row")'},
        {"id": "double-click", "t": 110, "type": "dblclick",
         "sel": 'locator("#row")', "ui": ui},
        {"id": "done", "t": 200, "type": "assert", "assertion": "visible",
         "expected": True, "sel": 'locator("#done")'},
    ]
    net = [
        {"id": 1, "t": 105, "phase": "req", "method": "POST",
         "url": "https://app.example/api/open", "body": "{}"},
        {"requestId": 1, "t": 106, "phase": "res", "method": "POST",
         "url": "https://app.example/api/open", "status": 200},
    ]

    _, prepared = prepare_steps(steps)
    spec = generate_spec(
        steps, net, start_url="https://app.example", name="double-click"
    )
    trace = generate_trace(
        steps, net, name="double-click", start_url="https://app.example"
    )

    assert [(step["type"], step["t"]) for step in prepared] == [
        ("dblclick", 100), ("assert", 200),
    ]
    assert "click_count=2" in spec
    assert "/api/open" in spec
    assert len(ts.node_ids(trace)) == 2
    double_click = trace["step_0001"]
    assert double_click["action"]["type"] == "DoubleClick"
    assert ts.verification(double_click) == {
        "responses": [{
            "method": "POST",
            "url": "https://app.example/api/open",
            "expectedStatus": 200,
            # 写请求必发：它是这一步真正的副作用，没发出去就是没做成。
            # 读请求会标 required=False —— 页面已处于目标状态时本来就不会重发。
            "required": True,
            # HTTP 层的事实桌面侧看不到，显式标出来免得 maa-fw 以为验过了
            "scope": ts.VERIFY_SCOPE_WEB,
            "request": {"body": {}},
        }],
    }
    assert double_click["next"] == ["step_0002"]


def test_trace_attaches_slow_response_to_action_that_started_request():
    steps = [
        {"id": "save", "t": 100, "type": "click", "sel": 'locator("#save")',
         "ui": {"pageRect": {"width": 20, "height": 10}, "templates": {
             "element": {"path": "flow.assets/step-0001.element.png"},
         }}},
        {"id": "next", "t": 200, "type": "click", "sel": 'locator("#next")',
         "ui": {"pageRect": {"width": 20, "height": 10}, "templates": {
             "element": {"path": "flow.assets/step-0002.element.png"},
         }}},
    ]
    net = [
        {"id": 1, "t": 110, "phase": "req", "method": "POST",
         "url": "https://app.example/api/save", "body": "{}"},
        {"requestId": 1, "t": 250, "phase": "res", "method": "POST",
         "url": "https://app.example/api/save", "status": 200},
    ]

    trace = generate_trace(steps, net, name="flow", start_url="https://app.example")

    assert ts.expected_responses(trace["step_0001"])[0]["url"].endswith(
        "/api/save"
    )
    assert not ts.expected_responses(trace["step_0002"])


def test_binary_request_body_is_stored_as_base64():
    class BinaryRequest:
        post_data_buffer = b"\x1f\x8b\x08\x00"

        @property
        def post_data(self):
            raise UnicodeDecodeError("utf-8", self.post_data_buffer, 1, 2, "invalid")

    fields = _request_body_fields(BinaryRequest())
    assert fields == {
        "body": None,
        "bodyBase64": "H4sIAA==",
        "bodyEncoding": "base64",
    }


def test_binary_request_trace_does_not_add_null_text_body():
    steps = [{
        "id": 1, "t": 100, "type": "click", "kind": "role",
        "sel": 'getByRole("button", { name: "Upload", exact: true })',
    }]
    net = [
        {"id": 1, "t": 110, "phase": "req", "method": "POST",
         "url": "https://app.example/api/upload", "bodyBase64": "H4sIAA==",
         "bodyEncoding": "base64"},
        {"requestId": 1, "t": 120, "phase": "res", "method": "POST",
         "url": "https://app.example/api/upload", "status": 200},
    ]

    trace = generate_trace(
        steps, net, start_url="https://app.example", name="upload"
    )

    request = ts.expected_responses(trace["step_0001"])[0]["request"]
    assert request == {"bodyBase64": "H4sIAA==", "bodyEncoding": "base64"}


def test_request_and_response_text_redact_nested_credentials():
    body = redact_text(
        '{"username":"alice","password":"private",'
        '"session":{"access_token":"token","safe":1}}'
    )

    assert json.loads(body) == {
        "username": "alice",
        "password": REDACTED,
        "session": {"access_token": REDACTED, "safe": 1},
    }


def test_request_body_fields_redact_before_raw_recording():
    class Request:
        post_data = '{"password":"private","safe":1}'

    assert json.loads(_request_body_fields(Request())["body"]) == {
        "password": REDACTED,
        "safe": 1,
    }


def test_large_response_is_redacted_before_it_is_truncated():
    body = json.dumps({"padding": "x" * 2500, "access_token": "private"})

    redacted = redact_text(body)

    assert "private" not in redacted
    assert json.loads(redacted)["access_token"] == REDACTED


def test_form_request_redacts_password_before_persistence():
    assert redact_text("username=alice&password=private") == (
        "username=alice&password=%3Credacted%3E"
    )


def test_generated_request_assertion_never_contains_old_recording_password():
    steps = [{
        "t": 100, "type": "click", "kind": "role",
        "sel": 'getByRole("button", { name: "Login", exact: true })',
    }]
    net = [
        {"id": 1, "t": 110, "phase": "req", "method": "POST",
         "url": "https://app.example/api/login",
         "body": '{"username":"alice","password":"private"}'},
        {"requestId": 1, "t": 120, "phase": "res", "method": "POST",
         "url": "https://app.example/api/login", "status": 200},
    ]

    spec = generate_spec(
        steps, net, start_url="https://app.example", name="login"
    )
    trace = generate_trace(
        steps, net, start_url="https://app.example", name="login"
    )

    assert "private" not in spec
    assert '"password": ANY_STR' in spec
    request = ts.expected_responses(trace["step_0001"])[0]["request"]
    assert request["body"]["password"] == REDACTED


def test_capture_storage_uses_existing_frames_without_opening_pages():
    class Context:
        def cookies(self):
            return [{"name": "sid", "value": "abc"}]

    class Frame:
        url = "https://app.example/path"

        def evaluate(self, expression):
            assert "localStorage" in expression
            return [{"name": "token", "value": "xyz"}]

    class Page:
        frames = [Frame()]

    assert _capture_storage(Context(), Page(), {}) == {
        "cookies": [{"name": "sid", "value": "abc"}],
        "origins": [{
            "origin": "https://app.example",
            "localStorage": [{"name": "token", "value": "xyz"}],
        }],
    }


def test_visible_clip_limits_element_to_viewport():
    assert _visible_clip({
        "rect": {"x": -5, "y": 10, "width": 30, "height": 100},
        "viewport": {"width": 20, "height": 50},
    }) == {"x": 0.0, "y": 10.0, "width": 20.0, "height": 40.0}


def test_capture_ui_template_records_rendered_asset(tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (32).to_bytes(4, "big") + (18).to_bytes(4, "big")

    class Shot:
        def screenshot(self, *, path):
            Path(path).write_bytes(png)
            return png

    class Locator:
        first = Shot()

    class Frame:
        def locator(self, css):
            assert css == "button#save"
            return Locator()

    step = {
        "type": "click",
        "css": "button#save",
        "ui": {"rect": {"x": 1, "y": 2, "width": 32, "height": 18}},
    }
    asset_dir = tmp_path / "demo.assets"
    _capture_ui_template({"frame": Frame()}, step, asset_dir, 3)

    assert (asset_dir / "step-0003.element.png").read_bytes() == png
    template = step["ui"]["templates"]["element"]
    assert template["path"] == "demo.assets/step-0003.element.png"
    assert template["width"] == 32
    assert template["height"] == 18


def test_capture_ui_templates_from_pre_click_frame_with_clipped_context(tmp_path):
    import cv2
    import numpy as np

    image = np.full((80, 100, 3), 240, dtype=np.uint8)
    image[10:25, 0:20] = (10, 40, 200)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    now = _now()
    step = {
        "t": now,
        "type": "click",
        "css": "button#edge",
        "ui": {
            "pageRect": {"x": 0, "y": 10, "width": 20, "height": 15},
            "pageViewport": {"width": 100, "height": 80},
        },
    }
    asset_dir = tmp_path / "edge.assets"

    _capture_ui_template(
        {}, step, asset_dir, 1,
        {"data": encoded.tobytes(), "t": now},
    )

    templates = step["ui"]["templates"]
    assert templates["element"]["width"] == 20
    assert templates["element"]["height"] == 15
    assert templates["context"]["width"] == 32
    assert templates["context"]["height"] == 37
    assert templates["context"]["elementOffset"] == {"x": 0, "y": 10}


def test_capture_ui_template_rejects_stale_pre_click_frame(tmp_path):
    fallback_png = _solid_png(20, 12)
    screenshots = []

    class Shot:
        def screenshot(self, *, path):
            screenshots.append(path)
            Path(path).write_bytes(fallback_png)
            return fallback_png

    class Locator:
        first = Shot()

    class Frame:
        def locator(self, css):
            assert css == "button#save"
            return Locator()

    step = {
        "t": 2501,
        "type": "click",
        "css": "button#save",
        "ui": {
            "pageRect": {"x": 10, "y": 10, "width": 20, "height": 12},
            "pageViewport": {"width": 100, "height": 80},
        },
    }
    asset_dir = tmp_path / "stale.assets"

    _capture_ui_template(
        {"frame": Frame()}, step, asset_dir, 1,
        {"data": _solid_png(100, 80), "t": 1000},
    )

    assert len(screenshots) == 1
    assert (asset_dir / "step-0001.element.png").read_bytes() == fallback_png
    assert set(step["ui"]["templates"]) == {"element"}


def test_crop_pre_frame_rejects_frame_after_action(tmp_path):
    step = {
        "t": 1200,
        "actionT": 1000,
        "ui": {
            "pageRect": {"x": 10, "y": 10, "width": 20, "height": 12},
            "pageViewport": {"width": 100, "height": 80},
        },
    }

    accepted = _crop_pre_frame(
        {"data": _solid_png(100, 80), "t": 1100},
        step,
        tmp_path / "future.assets",
        1,
    )

    assert not accepted
    assert "templates" not in step["ui"]


def test_select_pre_frame_uses_latest_frame_before_delayed_action():
    history = [
        {"data": b"before-old", "t": 1000},
        {"data": b"before-near", "t": 1400},
        {"data": b"after", "t": 1600},
    ]

    selected = _select_pre_frame(history, action_t=1500)

    assert selected == {"data": b"before-near", "t": 1400}


def test_stateful_action_never_falls_back_to_post_action_screenshot(tmp_path):
    class Frame:
        def locator(self, css):
            raise AssertionError("状态动作不得截取动作后的元素")

    step = {
        "t": 1000,
        "actionT": 900,
        "type": "switch",
        "css": "button#toggle",
        "ui": {"pageRect": {"x": 10, "y": 10, "width": 20, "height": 12}},
    }
    asset_dir = tmp_path / "switch.assets"

    _capture_ui_template({"frame": Frame()}, step, asset_dir, 1, pre_frame=None)

    assert "templates" not in step["ui"]
    assert not asset_dir.exists()


def test_capture_ui_template_captures_positioned_non_click_steps(tmp_path):
    step = {
        "t": 1000,
        "type": "switch",
        "css": "button#toggle",
        "ui": {
            "pageRect": {"x": 10, "y": 10, "width": 20, "height": 12},
            "pageViewport": {"width": 100, "height": 80},
        },
    }
    asset_dir = tmp_path / "switch.assets"

    _capture_ui_template(
        {}, step, asset_dir, 1,
        {"data": _solid_png(100, 80), "t": 999},
    )

    assert step["ui"]["templates"]["element"]["width"] == 20
    assert step["ui"]["templates"]["context"]["height"] == 34
    assert asset_dir.exists()


# ─────────────────────────────────────────────────────────────────────────────
# 回放工程的 fixture 装配
#
# 「录完给你一份能跑的 pytest」这个承诺，之前一条自检都没有 —— 而它靠的是
# 三处约定：fixture 能被第二个 conftest 导入、autouse 的没被 import * 丢掉、
# 登录态目录按运行时规则解析。任何一处坏掉，草稿都会以「没有可用的登录态」
# 这种指不到原因的方式失败。
# ─────────────────────────────────────────────────────────────────────────────

def _rec_fixtures():
    import importlib
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "assets"))
    return importlib.import_module("rec_fixtures")


def test_rec_fixtures_exports_every_fixture():
    """__all__ 必须列全 —— 尤其下划线开头的那个。

    `from rec_fixtures import *` 默认不带下划线开头的名字，而
    _auth_artifact_guard 是 autouse fixture：漏了它不会报错，
    只是登录产物再也没人清理，而且要等到某天泄露才发现。
    """
    mod = _rec_fixtures()
    exported = set(mod.__all__)
    declared = {
        name for name in dir(mod)
        if hasattr(getattr(mod, name), "_pytestfixturefunction")
    }
    missing = declared - exported
    assert not missing, f"这些 fixture 没进 __all__，import * 会丢掉：{sorted(missing)}"


def test_rec_fixtures_reexported_by_both_conftests():
    """两个 conftest 都必须真的把 fixture 转发出去。"""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for rel in ("assets/conftest.py", "recordings/conftest.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "from rec_fixtures import *" in text, f"{rel} 没有转发 fixture"


def test_auth_dir_follows_recorder_convention(tmp_path, monkeypatch):
    """登录态目录要和录制器同一套规则，否则草稿找不到录制器写下的登录态。

    录制器用 REC_STATE_DIR（默认相对 CWD 的 .auth）。conftest 以前写死成
    「自己同目录 / .auth」—— 用户工程里凑巧对，技能仓库里就错，
    而报错是「没有可用的登录态」，指不到真正的原因。
    """
    mod = _rec_fixtures()
    monkeypatch.setenv("REC_STATE_DIR", str(tmp_path / "custom-auth"))
    assert mod._auth_dir() == (tmp_path / "custom-auth").resolve()

    monkeypatch.delenv("REC_STATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".auth").mkdir()
    assert mod._auth_dir() == (tmp_path / ".auth").resolve()


def test_day_offset_survives_dst():
    """日偏移必须按 UTC 日历日算，不能按「本地零点 / 86400000」。

    后者在 UTC+0/+1 的时区跨夏令时会算错：BST 期间伦敦的本地零点落到
    **前一个 UTC 日**，floor 就跨错了格。纽约（UTC−5/−4）和上海（恒定 UTC+8）
    零点始终在同一 UTC 日内，是**运气**躲过去的 —— 所以在本机时区上跑
    浏览器自检抓不住这个回归，必须把时区当参数直接测算法。

    这里复刻注入层 dateValueMeta 里的两种编号方式，断言：
      旧算法在伦敦确实会错（否则这个测试就没有守护对象了）
      新算法在四个时区一整年都对
    """
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    DAY_MS = 86_400_000

    def local_day_no(d, tz):                      # 旧：本地零点 / 86400000
        return datetime(d.year, d.month, d.day, tzinfo=tz).timestamp() * 1000 // DAY_MS

    def utc_day_no(d):                            # 新：Date.UTC(y, m, d) / 86400000
        return datetime(d.year, d.month, d.day,
                        tzinfo=timezone.utc).timestamp() * 1000 // DAY_MS

    def wrong_days(numbering, tz):
        d, prev, bad = datetime(2026, 1, 1, tzinfo=tz), None, []
        for _ in range(400):
            n = numbering(d, tz) if numbering is local_day_no else numbering(d)
            if prev is not None and n - prev != 1:
                bad.append(d.date().isoformat())
            prev, d = n, d + timedelta(days=1)
        return bad

    london = ZoneInfo("Europe/London")
    assert wrong_days(local_day_no, london), \
        "旧算法在伦敦竟然没出错 —— 这个测试失去了守护对象，检查它是否还有效"

    for name in ("Europe/London", "America/New_York",
                 "Australia/Sydney", "Asia/Shanghai"):
        bad = wrong_days(utc_day_no, ZoneInfo(name))
        assert not bad, f"{name} 上 UTC 编号出错：{bad[:3]}"


# ─────────────────────────── v2 轨迹形状：maa-fw 那一侧的契约 ───────────────────────────
#
# 这些判据守的是「maa-fw 能不能加载我们的产物」。这件事在我们自己的回放里
# **看不出来** —— 我们的回放器不碰 confidence_policy / gui_target，也不会去
# coerce 节点。所以坏了只有 maa-fw 那边会炸，而那时候已经太晚了。

def _v2_trace():
    steps = [
        {"id": "c1", "t": 100, "type": "click", "sel": 'locator("#go")',
         "kind": "id", "label": "去", "url": "https://app.example/list",
         "ui": {
             "click": {"rx": 0.2, "ry": 0.8},
             "pageRect": {"x": 10, "y": 20, "width": 40, "height": 16},
             "templates": {
                 "context": {"path": "f.assets/step_0001.context.png",
                             "width": 80, "height": 40},
                 "element": {"path": "f.assets/step_0001.element.png",
                             "width": 40, "height": 16},
             },
         }},
        {"id": "a1", "t": 200, "type": "assert", "assertion": "text",
         "expected": "已保存", "sel": 'locator("#msg")', "kind": "id"},
        {"id": "a2", "t": 300, "type": "assert", "assertion": "text",
         "expected": "12:30", "sel": 'locator("#clock")', "kind": "id",
         "expectedFrom": {"kind": "localtime", "format": "%H:%M",
                          "match": "contains"}},
    ]
    return generate_trace(steps, [], name="flow", start_url="https://app.example/list")


def test_every_node_carries_recognition_and_action():
    """MaaFramework 的节点必须同时有 recognition 和 action。

    映射不到图像识别的步骤用 DirectHit（无条件命中）+ DoNothing，而不是
    把键省掉 —— 省掉的话 _coerce_node 会填出一个 DirectHit/DoNothing，
    结果一样，但我们就失去了「这一步是有意不靠视觉」的记录。
    """
    trace = _v2_trace()
    for node_id, node in ts.nodes(trace):
        assert node["recognition"].get("type"), node_id
        assert node["action"].get("type"), node_id
        assert isinstance(node["next"], list), node_id


def test_node_names_use_the_charset_maa_fw_normalises_to():
    """maa-fw 的 node_name() 把名字规范到 [0-9A-Za-z_]。

    我们主动对齐，否则同一个节点在两边有两个名字，日志和统计对不上号。
    """
    import re
    for node_id, _ in ts.nodes(_v2_trace()):
        assert re.fullmatch(r"[0-9A-Za-z_]+", node_id), node_id


def test_strict_attach_dicts_only_carry_keys_maa_fw_accepts():
    """confidence_policy / gui_target 是用 `**` 反序列化的，多一个键就炸。

    对应 maa-fw 的 MaaNodeRunner._coerce_node：
        ConfidencePolicy(**attach["confidence_policy"])
        GuiTarget(**attach["gui_target"])
    所以我们自己的额外参数（尺度列表、歧义边界、多模板顺序）必须进 provenance。
    """
    trace = _v2_trace()
    click = trace["step_0001"]
    assert set(ts.attach(click)["confidence_policy"]) <= ts.STRICT_CONFIDENCE_KEYS
    assert set(ts.attach(click)["gui_target"]) <= ts.STRICT_GUI_TARGET_KEYS
    # 那些额外参数确实还在，只是搬到了 provenance
    prov = ts.provenance(click)
    assert prov["scaleFactors"] and prov["ambiguityMargin"]
    assert prov["templateOrder"] == ["context", "element"]


def test_validate_trace_catches_a_key_maa_fw_would_choke_on():
    """守护上一条判据本身。

    没有这个反向检查，`strict_attach_errors` 可以退化成永远返回空列表，
    上面那条断言照样绿 —— 它只看我们此刻恰好没多写键。
    """
    from replay_trace import TraceReplayError, validate_trace

    trace = _v2_trace()
    validate_trace(trace)                       # 先证明它本来是好的
    ts.attach(trace["step_0001"])["confidence_policy"]["scale_factors"] = [1.0]
    with pytest.raises(TraceReplayError, match="maa-fw 不认的键"):
        validate_trace(trace)


def test_meta_node_is_inert_under_maa_fw_skip_rule():
    """$meta 不能被当成一步执行。

    MaaNodeRunner.run 不传 start_nodes 时会把**整张节点表**塞进队列，
    $meta 也在里面。它的 recognition 是 DirectHit（无条件命中），真被执行
    就会凭空多出一步。靠 max_hit=1 配 stats.hit_count=1 让 _should_skip_node
    无条件跳过它 —— 这里复刻那条规则：

        if node.max_hit and hit_count >= node.max_hit: return True
    """
    meta_node = _v2_trace()[ts.META_KEY]
    max_hit = meta_node.get("max_hit", 0)
    hit_count = ts.attach(meta_node)["stats"]["hit_count"]
    assert max_hit and hit_count >= max_hit, (max_hit, hit_count)
    # 也不能有出边，否则它会把真步骤拉进自己的分支
    assert meta_node["next"] == []


def test_meta_is_neither_entry_nor_anyones_next():
    trace = _v2_trace()
    assert ts.meta(trace)["entry"] != ts.META_KEY
    for _, node in ts.nodes(trace):
        assert ts.META_KEY not in node["next"]


def test_has_template_does_not_count_direct_hit_as_visual_fallback():
    """v2 里每个节点都有 recognition —— 「有没有视觉兜底」不能再看它是否存在。

    照旧那么写会把所有无模板步骤都当成有视觉兜底：DOM 失败时不再原样抛出
    真正的原因，而是去做一次注定失败的视觉匹配，最后报「视觉匹配分数不足」。
    那正是这套东西以前最难查的一类误导性报错。
    """
    trace = _v2_trace()
    assert ts.has_template(trace["step_0001"]) is True
    assert trace["step_0002"]["recognition"]["type"] != "TemplateMatch"
    assert ts.has_template(trace["step_0002"]) is False
    # 有 TemplateMatch 的名头但模板丢了，也不算有
    faked = {"recognition": {"type": "TemplateMatch", "param": {}}, "attach": {}}
    assert ts.has_template(faked) is False


def test_click_point_travels_as_a_ratio_not_pixels():
    """落点用 target_ratio（比例），不用 target_offset（像素）。

    匹配框会按尺度缩放，导出时算出的像素偏移在尺度≠1 时就偏了 —— 跨分辨率
    复用模板正是这套东西的全部意义。maa-fw 自己也是这么想的，它的
    _observed_target_ratio 就是比例。
    """
    param = _v2_trace()["step_0001"]["action"]["param"]
    assert param["target"] is True
    assert param["target_offset"] == [0, 0, 0, 0]
    assert param["target_ratio"] == [0.2, 0.8]      # 录制时那一下的真实落点
    # 回放器读回来的必须是同一个点，不能退化成框中心
    assert ts.relative_point(_v2_trace()["step_0001"]) == {"x": 0.2, "y": 0.8}


def test_static_text_assertion_becomes_an_ocr_node_maa_fw_can_verify():
    """能映射的断言要变成真节点，不是一律标 web-only 了事。

    maa-fw 的 SKILL.md 把验证当一等公民（wait_text / OCR expected），
    所以「页面上有这段文字」这类断言在桌面侧是**验得了**的。
    """
    node = _v2_trace()["step_0002"]
    assert node["recognition"]["type"] == "OCR"
    assert node["recognition"]["param"]["expected"] == ["已保存"]
    assert node["action"]["type"] == "DoNothing"
    # 断言规格照旧留着，我们自己的回放走 DOM 断言那条路
    spec = ts.assertion_of(node)
    assert spec == {"assertion": "text", "expected": "已保存"}
    assert "scope" not in spec, "映射成 OCR 了就不该再标 web-only"


def test_runtime_computed_expectation_is_marked_web_only():
    """运行时才算的期望值写不成静态 OCR expected。

    「显示的是当前时间」的期望值由回放此刻的时钟决定 —— 硬塞一个录制时的
    字面量进 OCR，等于把这条断言变成一颗定时炸弹：当场绿、隔天必挂。
    标成 web-only 让桌面侧显式跳过，比假装验过要好。
    """
    node = _v2_trace()["step_0003"]
    assert node["recognition"]["type"] == "DirectHit"
    spec = ts.assertion_of(node)
    assert spec["scope"] == ts.VERIFY_SCOPE_WEB
    assert spec["expectedFrom"]["kind"] == "localtime"


def test_network_expectations_are_marked_web_only():
    """HTTP 状态码和请求体桌面侧根本看不到。"""
    steps = [{"id": "c1", "t": 100, "type": "click", "sel": 'locator("#save")',
              "kind": "id", "ui": {"click": {"rx": 0.5, "ry": 0.5},
                                   "pageRect": {"width": 10, "height": 10},
                                   "templates": {"element": {"path": "a.png",
                                                             "width": 10,
                                                             "height": 10}}}}]
    net = [
        {"id": 1, "t": 101, "phase": "req", "method": "POST",
         "url": "https://app.example/api/save", "body": "{}"},
        {"requestId": 1, "t": 102, "phase": "res", "method": "POST",
         "url": "https://app.example/api/save", "status": 200},
    ]
    trace = generate_trace(steps, net, name="flow", start_url="https://app.example/")
    responses = ts.expected_responses(trace["step_0001"])
    assert responses and all(r["scope"] == ts.VERIFY_SCOPE_WEB for r in responses)


def test_rewritten_existence_assertion_still_maps_to_ocr():
    """「文本同义反复」改写过的断言不能因为形状变了就退成 web-only。

    prepare_steps 会把「用文本定位元素、再断言它的文本等于那段文本」改写成
    存在性断言 —— 那只是换了个更诚实的 **web 断言写法**，用户的意思没变，
    还是「这段文字应该在」，照样是 OCR 的主场。

    这条判据很关键：实测录出来的断言**绝大多数**都会走那条改写路径。漏掉它，
    maa-fw 侧几乎一条断言都验不了，「统一轨迹」就只剩个形状。
    """
    steps = [
        {"id": "a1", "t": 100, "type": "assert", "assertion": "text",
         "expected": "maa-fw", "kind": "text",
         "sel": 'getByText("maa-fw", { exact: true })'},
    ]
    trace = generate_trace(steps, [], name="flow", start_url="https://app.example/")
    node = trace["step_0001"]
    # 先证明改写真的发生了，否则这条判据在验一件没发生的事
    spec = ts.assertion_of(node)
    assert spec["assertion"] == "visible" and spec["expected"] is True, spec
    assert node["recognition"]["type"] == "OCR", node["recognition"]
    assert node["recognition"]["param"]["expected"] == ["maa-fw"]
    assert "scope" not in spec, "改写过的断言 maa-fw 照样验得了，不该标 web-only"


def test_existence_assertion_without_a_text_anchor_stays_web_only():
    """断言对象不是文字时才该退成 web-only。

    守护上一条：没有这个反向检查，「一律映射成 OCR」也能让上面那条绿 ——
    而那会给桌面侧一个 expected 为空的 OCR 节点，永远匹配不上。
    """
    steps = [
        {"id": "a1", "t": 100, "type": "assert", "assertion": "visible",
         "expected": True, "kind": "id", "sel": 'locator("#chart")'},
    ]
    trace = generate_trace(steps, [], name="flow", start_url="https://app.example/")
    node = trace["step_0001"]
    assert node["recognition"]["type"] == "DirectHit"
    assert ts.assertion_of(node)["scope"] == ts.VERIFY_SCOPE_WEB
