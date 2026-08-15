from pathlib import Path

import pytest

from generate_spec import generate_spec
from rec_assert import ANY_NUM
from rec_config import load_config
from record import (
    _capture_storage,
    _capture_ui_template,
    _now,
    _request_body_fields,
    _visible_clip,
)
from selector_py import to_python


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
