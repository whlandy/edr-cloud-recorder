import json

import cv2
import numpy as np
import pytest

from replay_trace import TraceReplayError, evaluate_trace, replay_trace, validate_trace


def _trace(node, *, status="ready"):
    return {
        "schema": "edr.success-trace/v1",
        "name": "save-flow",
        "startUrl": "https://app.example/form",
        "status": status,
        "entry": "step-0001",
        "steps": {"step-0001": {"status": "ready", "next": None, **node}},
    }


class _Request:
    method = "POST"
    post_data_json = {"name": "alice", "extra": True}


class _Response:
    url = "https://app.example/api/save"
    request = _Request()

    def __init__(self, status=200):
        self.status = status

    def text(self):
        return '{"saved":true}'


class _ResponseInfo:
    value = None


class _ResponseContext:
    def __init__(self, page, predicate):
        self.page = page
        self.predicate = predicate
        self.info = _ResponseInfo()

    def __enter__(self):
        self.page.events.append("listen")
        self.page.listeners.append(self)
        return self.info

    def __exit__(self, exc_type, exc, traceback):
        self.page.listeners.remove(self)
        if exc_type is None and self.info.value is None:
            raise TimeoutError("response listener did not observe a matching response")


class _Locator:
    def __init__(self, page):
        self.page = page

    def wait_for(self, *, state, timeout):
        assert state == "visible"
        assert timeout == 5000

    def click(self):
        self.page.events.append("click")
        response = _Response(self.page.response_status)
        for listener in list(self.page.listeners):
            if listener.predicate(response):
                listener.info.value = response


class _NetworkPage:
    def __init__(self, response_status=200):
        self.response_status = response_status
        self.events = []
        self.listeners = []

    def goto(self, url):
        self.events.append(("goto", url))

    def locator(self, selector):
        assert selector == "#save"
        return _Locator(self)

    def expect_response(self, predicate, *, timeout):
        assert timeout == 5000
        return _ResponseContext(self, predicate)


def _network_trace(expected_status=200):
    return _trace({
        "selector": {"sel": 'locator("#save")'},
        "action": {"type": "Click", "param": {}},
        "expect": {"responses": [{
            "method": "POST",
            "url": "https://app.example/api/save",
            "expectedStatus": expected_status,
            "request": {"body": {"name": "alice"}},
        }]},
    })


def test_replay_navigates_and_arms_response_before_click(tmp_path):
    page = _NetworkPage()
    output = tmp_path / "execution.json"

    execution = replay_trace(page, _network_trace(), execution_path=output)

    assert page.events == [
        ("goto", "https://app.example/form"),
        "listen",
        "click",
    ]
    assert execution["status"] == "success"
    assert execution["steps"][0]["responses"][0]["ok"] is True
    assert json.loads(output.read_text(encoding="utf-8")) == execution
    assert evaluate_trace(_network_trace(), execution)["score"] == 100


def test_replay_response_failure_is_not_scored_as_success():
    execution = replay_trace(_NetworkPage(response_status=500), _network_trace())
    report = evaluate_trace(_network_trace(), execution)

    assert execution["status"] == "failed"
    assert "响应状态不符" in execution["steps"][0]["error"]
    assert report["taskSuccess"] is False
    assert report["networkAssertionRate"] == 0
    assert report["score"] < 100


def test_replay_rejects_mismatched_binary_request_body():
    trace = _network_trace()
    trace["steps"]["step-0001"]["expect"]["responses"][0]["request"] = {
        "bodyBase64": "bm90LXRoZS1ib2R5",
        "bodyEncoding": "base64",
    }
    _Request.post_data_buffer = b"actual-body"
    try:
        execution = replay_trace(_NetworkPage(), trace)
    finally:
        del _Request.post_data_buffer

    assert execution["status"] == "failed"
    assert "二进制请求体" in execution["steps"][0]["error"]
    assert evaluate_trace(trace, execution)["taskSuccess"] is False


def test_network_score_cannot_be_inflated_by_extra_responses():
    execution = {
        "status": "success",
        "steps": [{
            "nodeId": "step-0001",
            "status": "success",
            "actualAction": "Click",
            "responses": [{"ok": True}, {"ok": True}, {"ok": True}],
        }],
    }

    report = evaluate_trace(_network_trace(), execution)

    assert report["networkAssertionRate"] == 1
    assert report["score"] == 100


def _pattern(width=20, height=16):
    image = np.full((height, width, 3), 235, dtype=np.uint8)
    cv2.rectangle(image, (1, 1), (width - 2, height - 2), (20, 90, 210), 2)
    cv2.line(image, (4, height - 4), (width - 5, 4), (220, 50, 40), 2)
    return image


def _png(image):
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def test_visual_only_replay_matches_then_clicks(tmp_path):
    template = _pattern()
    asset_dir = tmp_path / "flow.assets"
    asset_dir.mkdir()
    cv2.imwrite(str(asset_dir / "step-0001.element.png"), template)
    screen = np.full((90, 140, 3), 248, dtype=np.uint8)
    screen[30:46, 50:70] = template

    class Mouse:
        clicked = None

        def click(self, x, y, **kwargs):
            self.clicked = (x, y, kwargs)

    class Page:
        mouse = Mouse()

        def goto(self, url):
            assert url == "https://app.example/form"

        def screenshot(self):
            return _png(screen)

        def evaluate(self, expression):
            return {"width": 140, "height": 90}

        def locator(self, selector):
            raise AssertionError("visual_only 不应使用 DOM locator")

    trace = _trace({
        "selector": {"sel": 'locator("#save")'},
        "geometry": {"pageRect": {"width": 20, "height": 16}},
        "recognition": {
            "type": "TemplateMatch",
            "templates": {"element": {
                "path": "flow.assets/step-0001.element.png",
                "width": 20,
                "height": 16,
            }},
        },
        "action": {
            "type": "Click",
            "param": {"relativePoint": {"x": 0.25, "y": 0.75}},
        },
    })
    page = Page()

    execution = replay_trace(page, trace, template_root=tmp_path, targeting="visual_only")

    assert execution["status"] == "success"
    assert page.mouse.clicked[:2] == pytest.approx((55, 42), abs=0.5)
    assert execution["steps"][0]["target"]["mode"] == "visual"
    assert evaluate_trace(trace, execution)["averageVisualMatchScore"] > 0.9


def test_validate_trace_rejects_cycle():
    trace = _network_trace()
    trace["steps"]["step-0001"]["next"] = "step-0001"

    with pytest.raises(TraceReplayError, match="存在环"):
        validate_trace(trace)
