from pathlib import Path

import cv2
import numpy as np
import pytest

from rec_visual import VisualMatchError, locate_template, visual_click


def _pattern(width=24, height=18):
    image = np.full((height, width, 3), (235, 235, 235), dtype=np.uint8)
    cv2.rectangle(image, (1, 1), (width - 2, height - 2), (20, 90, 210), 2)
    cv2.line(image, (4, height - 4), (width - 5, 4), (220, 50, 40), 2)
    cv2.circle(image, (width // 2, height // 2), 3, (30, 180, 70), -1)
    return image


def _png(image) -> bytes:
    ok, data = cv2.imencode(".png", image)
    assert ok
    return data.tobytes()


def test_locate_template_handles_scale_change(tmp_path):
    template = _pattern()
    path = tmp_path / "button.png"
    cv2.imwrite(str(path), template)
    scaled = cv2.resize(template, None, fx=1.25, fy=1.25, interpolation=cv2.INTER_CUBIC)
    screen = np.full((140, 220, 3), 248, dtype=np.uint8)
    screen[52:52 + scaled.shape[0], 73:73 + scaled.shape[1]] = scaled

    match = locate_template(_png(screen), path, expected_scale=1.25)

    assert abs(match.x - 73) <= 1
    assert abs(match.y - 52) <= 1
    assert match.scale == 1.25
    assert match.score > 0.9


def test_locate_low_variance_template(tmp_path):
    template = np.full((14, 18, 3), (24, 132, 218), dtype=np.uint8)
    path = tmp_path / "flat.png"
    cv2.imwrite(str(path), template)
    screen = np.full((80, 120, 3), (238, 241, 244), dtype=np.uint8)
    screen[31:45, 47:65] = template

    match = locate_template(_png(screen), path, threshold=0.95)

    assert (match.x, match.y, match.w, match.h) == (47, 31, 18, 14)
    assert match.score == pytest.approx(1.0)


def test_locate_template_rejects_unrelated_screen(tmp_path):
    path = tmp_path / "target.png"
    cv2.imwrite(str(path), _pattern())
    screen = np.full((90, 130, 3), 127, dtype=np.uint8)

    with pytest.raises(VisualMatchError, match="分数不足"):
        locate_template(_png(screen), path, threshold=0.9)


def test_locate_template_rejects_duplicate_icons(tmp_path):
    template = _pattern()
    path = tmp_path / "duplicate.png"
    cv2.imwrite(str(path), template)
    screen = np.full((100, 180, 3), 248, dtype=np.uint8)
    screen[20:38, 20:44] = template
    screen[20:38, 110:134] = template

    with pytest.raises(VisualMatchError, match="不唯一"):
        locate_template(_png(screen), path)


def test_visual_click_keeps_fast_dom_path_cold(tmp_path):
    class Locator:
        def wait_for(self, *, state, timeout):
            assert state == "visible"
            assert timeout == 1500

        def click(self):
            pass

    class Page:
        def screenshot(self):
            raise AssertionError("DOM 成功时不应截图")

    assert visual_click(
        Page(), Locator(), template_root=tmp_path, ui={}
    ) == "dom"


def test_visual_click_uses_recorded_relative_point(tmp_path):
    template = _pattern(20, 16)
    asset_dir = tmp_path / "flow.assets"
    asset_dir.mkdir()
    path = asset_dir / "step-0001.element.png"
    cv2.imwrite(str(path), template)
    screen = np.full((100, 160, 3), 248, dtype=np.uint8)
    screen[30:46, 40:60] = template

    class Locator:
        def wait_for(self, *, state, timeout):
            raise RuntimeError("selector stale")

    class Mouse:
        clicked = None

        def click(self, x, y):
            self.clicked = (x, y)

    class Page:
        mouse = Mouse()

        def screenshot(self):
            return _png(screen)

        def evaluate(self, expression):
            return {"width": 160, "height": 100}

    page = Page()
    ui = {
        "pageRect": {"x": 5, "y": 5, "width": 20, "height": 16},
        "click": {"rx": 0.25, "ry": 0.75},
        "templates": {
            "element": {
                "path": "flow.assets/step-0001.element.png",
                "width": 20,
                "height": 16,
            },
        },
    }

    assert visual_click(
        page, Locator(), template_root=tmp_path, ui=ui
    ) == "visual"
    assert page.mouse.clicked == pytest.approx((45, 42), abs=0.5)


def test_visual_click_maps_context_offset_after_scaling(tmp_path):
    element = _pattern(20, 16)
    context = np.full((28, 32, 3), (235, 239, 243), dtype=np.uint8)
    context[6:22, 6:26] = element
    scaled = cv2.resize(context, (40, 35), interpolation=cv2.INTER_LINEAR)
    screen = np.full((100, 140, 3), 250, dtype=np.uint8)
    screen[30:65, 40:80] = scaled
    asset_dir = tmp_path / "flow.assets"
    asset_dir.mkdir()
    cv2.imwrite(str(asset_dir / "step-0001.context.png"), context)

    class Locator:
        def wait_for(self, *, state, timeout):
            raise RuntimeError("selector stale")

    class Mouse:
        clicked = None

        def click(self, x, y):
            self.clicked = (x, y)

    class Page:
        mouse = Mouse()

        def screenshot(self):
            return _png(screen)

        def evaluate(self, expression):
            return {"width": 140, "height": 100}

    page = Page()
    ui = {
        "pageRect": {"width": 20, "height": 16},
        "click": {"rx": 0.25, "ry": 0.75},
        "templates": {
            "element": {"width": 20, "height": 16},
            "context": {
                "path": "flow.assets/step-0001.context.png",
                "width": 32,
                "height": 28,
                "elementOffset": {"x": 6, "y": 6},
            },
        },
    }

    assert visual_click(
        page, Locator(), template_root=tmp_path, ui=ui
    ) == "visual"
    assert page.mouse.clicked == pytest.approx((53.75, 52.5), abs=0.5)


def test_visual_click_missing_template_never_clicks(tmp_path):
    class Locator:
        def wait_for(self, *, state, timeout):
            raise RuntimeError("selector stale")

    class Mouse:
        clicked = None

        def click(self, x, y):
            self.clicked = (x, y)

    class Page:
        mouse = Mouse()

    page = Page()
    with pytest.raises(VisualMatchError, match="没有可用视觉模板"):
        visual_click(
            page,
            Locator(),
            template_root=tmp_path,
            ui={"pageRect": {"width": 20, "height": 16}},
        )

    assert page.mouse.clicked is None


def test_visual_click_never_retries_after_dom_click_started(tmp_path):
    class Locator:
        def wait_for(self, *, state, timeout):
            pass

        def click(self):
            raise RuntimeError("request sent, navigation timed out")

    class Page:
        def screenshot(self):
            raise AssertionError("click 已开始后不得视觉补点")

    with pytest.raises(RuntimeError, match="navigation timed out"):
        visual_click(Page(), Locator(), template_root=tmp_path, ui={})


def test_visual_double_click_uses_template_match(tmp_path):
    template = _pattern(20, 16)
    asset_dir = tmp_path / "flow.assets"
    asset_dir.mkdir()
    path = asset_dir / "step-0001.element.png"
    cv2.imwrite(str(path), template)
    screen = np.full((80, 120, 3), 248, dtype=np.uint8)
    screen[25:41, 35:55] = template

    class Locator:
        def wait_for(self, *, state, timeout):
            raise RuntimeError("selector stale")

    class Mouse:
        clicked = None

        def click(self, x, y, **kwargs):
            self.clicked = (x, y, kwargs)

    class Page:
        mouse = Mouse()

        def screenshot(self):
            return _png(screen)

        def evaluate(self, expression):
            return {"width": 120, "height": 80}

    page = Page()
    ui = {
        "pageRect": {"width": 20, "height": 16},
        "templates": {
            "element": {
                "path": "flow.assets/step-0001.element.png",
                "width": 20,
                "height": 16,
            },
        },
    }

    assert visual_click(
        page, Locator(), template_root=tmp_path, ui=ui, click_count=2
    ) == "visual"
    assert page.mouse.clicked[:2] == pytest.approx((45, 33), abs=0.5)
    assert page.mouse.clicked[2] == {"click_count": 2}
