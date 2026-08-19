import json
from pathlib import Path

import pytest

from generate_spec import generate_spec, prepare_steps
from generate_trace import TRACE_SCHEMA, generate_trace
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

    assert trace["schema"] == TRACE_SCHEMA
    assert trace["entry"] == "step-0001"
    assert [step["sourceStepId"] for step in trace["steps"].values()] == [
        "input", "check", "assert", "missing-click",
    ]
    fill, check, assertion, missing = trace["steps"].values()
    assert fill["recognition"]["type"] == "TemplateMatch"
    assert fill["recognition"]["templateOrder"] == ["context", "element"]
    assert fill["recognition"]["templates"] == input_templates
    assert fill["sourceStepIds"] == ["focus-input", "input"]
    assert fill["action"] == {
        "type": "InputText",
        "param": {"text": "alice", "focusBeforeInput": True},
    }
    assert fill["status"] == "ready"
    assert fill["next"] == "step-0002"
    assert check["recognition"]["type"] == "TemplateMatch"
    assert check["action"]["type"] == "Check"
    assert assertion["action"]["type"] == "Assert"
    assert missing["status"] == "missing_template"
    assert missing["next"] is None
    assert trace["status"] == "incomplete"


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
    assert len(trace["steps"]) == 2
    double_click = trace["steps"]["step-0001"]
    assert double_click["action"]["type"] == "DoubleClick"
    assert double_click["expect"] == {
        "responses": [{
            "method": "POST",
            "url": "https://app.example/api/open",
            "expectedStatus": 200,
            # 写请求必发：它是这一步真正的副作用，没发出去就是没做成。
            # 读请求会标 required=False —— 页面已处于目标状态时本来就不会重发。
            "required": True,
            "request": {"body": {}},
        }],
    }
    assert double_click["next"] == "step-0002"


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

    assert trace["steps"]["step-0001"]["expect"]["responses"][0]["url"].endswith(
        "/api/save"
    )
    assert "expect" not in trace["steps"]["step-0002"]


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

    request = trace["steps"]["step-0001"]["expect"]["responses"][0]["request"]
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
    request = trace["steps"]["step-0001"]["expect"]["responses"][0]["request"]
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
