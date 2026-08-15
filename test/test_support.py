from pathlib import Path

import pytest

from generate_spec import generate_spec, prepare_steps
from generate_trace import TRACE_SCHEMA, generate_trace
from rec_assert import ANY_NUM
from rec_config import load_config
from record import (
    _capture_storage,
    _capture_ui_template,
    _crop_pre_frame,
    _now,
    _request_body_fields,
    _select_pre_frame,
    _visible_clip,
)
from selector_py import to_python


def _solid_png(width: int, height: int) -> bytes:
    import cv2
    import numpy as np

    ok, encoded = cv2.imencode(
        ".png", np.full((height, width, 3), 240, dtype=np.uint8)
    )
    assert ok
    return encoded.tobytes()


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
