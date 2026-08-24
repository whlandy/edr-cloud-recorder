import json

import cv2
import numpy as np
import pytest

from replay_trace import TraceReplayError, _assert_node_page, _locator, _page_for_node
from replay_trace import evaluate_trace as _evaluate_trace
from replay_trace import replay_trace as _replay_trace
from replay_trace import validate_trace as _validate_trace
from rec_secrets import REDACTED
from rec_session import write_session_snapshot
from trace_v1 import node_to_v2, to_v2


# 测试用平铺节点表达意图，进回放器之前统一翻成 v2 —— 翻译逻辑见 trace_v1.py
def replay_trace(page, trace, **kwargs):
    return _replay_trace(page, to_v2(trace) if isinstance(trace, dict) else trace, **kwargs)


def evaluate_trace(golden, execution):
    return _evaluate_trace(to_v2(golden), execution)


def validate_trace(trace):
    return _validate_trace(to_v2(trace))


def _trace(node, *, status="ready"):
    return {
        "schema": "edr.success-trace/v2",
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


def test_trace_path_restores_its_recorded_session_before_navigation(tmp_path):
    events = []

    class Context:
        def clear_cookies(self):
            events.append("clear_cookies")

        def add_cookies(self, cookies):
            events.append(("add_cookies", cookies[0]["value"]))

        def add_init_script(self, *, script):
            assert "session-token" in script
            events.append("add_init_script")

    page = _NetworkPage()
    page.context = Context()
    page.events = events
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps(to_v2(_network_trace())), encoding="utf-8")
    write_session_snapshot(
        tmp_path / ".auth",
        {
            "cookies": [{
                "name": "sid", "value": "recorded-cookie",
                "domain": "app.example", "path": "/",
            }],
            "origins": [],
        },
        {"token": "session-token"},
        session_origin="https://app.example",
    )

    execution = _replay_trace(page, trace_path)

    assert execution["status"] == "success"
    assert events[:4] == [
        "clear_cookies",
        ("add_cookies", "recorded-cookie"),
        "add_init_script",
        ("goto", "https://app.example/form"),
    ]


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


def test_replay_accepts_redacted_request_and_response_credentials(monkeypatch):
    trace = _network_trace()
    expected = trace["steps"]["step-0001"]["expect"]["responses"][0]
    expected["request"]["body"]["password"] = REDACTED
    expected["expectedBody"] = json.dumps({"access_token": REDACTED, "safe": 1})
    monkeypatch.setattr(
        _Request, "post_data_json", {"name": "alice", "password": "private"}
    )
    monkeypatch.setattr(
        _Response, "text", lambda self: json.dumps({"access_token": "token", "safe": 1})
    )

    execution = replay_trace(_NetworkPage(), trace)

    assert execution["status"] == "success"


def test_network_score_cannot_be_inflated_by_extra_responses():
    execution = {
        "schema": "edr.execution-trace/v1",
        "goldenSchema": "edr.success-trace/v2",
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


def test_visual_only_press_key_reuses_focus_from_same_visual_target(monkeypatch):
    class Match:
        score = 0.95
        verify_score = 0.9
        scale = 1.0

    class Target:
        x = 10
        y = 20
        kind = "element"
        match = Match()

    monkeypatch.setattr("replay_trace.locate_visual_target", lambda *args, **kwargs: Target())

    class Keyboard:
        pressed = []

        def press(self, key):
            self.pressed.append(key)

    class Page:
        keyboard = Keyboard()

        class Mouse:
            def click(self, x, y, **kwargs):
                pass

        mouse = Mouse()

        def goto(self, url):
            pass

        def locator(self, selector):
            raise AssertionError("visual_only 的按键步骤不应重新使用 DOM 定位")

    trace = _trace({
        "selector": {"sel": 'locator("#query")'},
        "geometry": {"pageRect": {"width": 20, "height": 10}},
        "recognition": {"type": "TemplateMatch", "templates": {"element": {}}},
        "action": {"type": "Click", "param": {}},
        "next": "step-0002",
    })
    trace["steps"]["step-0002"] = {
        "status": "ready",
        "selector": {"sel": 'locator("#query")'},
        "action": {"type": "PressKey", "param": {"key": "Enter"}},
        "next": None,
    }

    execution = replay_trace(Page(), trace, targeting="visual_only")

    assert execution["status"] == "success"
    assert execution["steps"][1]["target"] == {"mode": "keyboard"}
    assert Page.keyboard.pressed == ["Enter"]


def test_visual_only_press_key_rejects_unproven_focus():
    class Page:
        def goto(self, url):
            pass

    trace = _trace({
        "selector": {"sel": 'locator("#query")'},
        "action": {"type": "PressKey", "param": {"key": "Enter"}},
    })

    execution = replay_trace(Page(), trace, targeting="visual_only")

    assert execution["status"] == "failed"
    assert "焦点" in execution["steps"][0]["error"]


def test_set_switch_waits_for_async_target_state():
    class Switch:
        clicked = False
        reads = 0

        def wait_for(self, **kwargs):
            pass

        def get_attribute(self, name):
            assert name == "aria-checked"
            self.reads += 1
            return "true" if self.clicked and self.reads >= 3 else "false"

        def click(self):
            self.clicked = True

    switch = Switch()

    class Page:
        def goto(self, url):
            pass

        def locator(self, selector):
            return switch

    trace = _trace({
        "selector": {"sel": 'locator("#switch")'},
        "action": {
            "type": "SetSwitch",
            "param": {"state": True, "via": {"type": "aria"}},
        },
    })

    execution = replay_trace(Page(), trace, timeout_ms=500)

    assert execution["status"] == "success"
    assert switch.clicked is True
    assert switch.reads == 3


def _switch_page(state, *, on_click=None):
    class Switch:
        clicks = 0

        def wait_for(self, **kwargs):
            pass

        def get_attribute(self, name):
            assert name == "aria-checked"
            return "true" if state["on"] else "false"

        def click(self):
            self.clicks += 1
            if on_click:
                on_click()

    switch = Switch()

    class Page:
        def goto(self, url):
            pass

        def locator(self, selector):
            return switch

    return Page(), switch


def test_set_switch_skips_when_already_in_target_state():
    """拨开关必须是幂等的：轨迹会改动策略状态，同一条连跑两次不能一次成一次败。"""
    page, switch = _switch_page({"on": True})
    trace = _trace({
        "selector": {"sel": 'locator("#switch")'},
        "action": {
            "type": "SetSwitch",
            "param": {"state": True, "via": {"type": "aria"}},
        },
    })

    execution = replay_trace(page, trace, timeout_ms=500)

    assert execution["status"] == "success"
    assert switch.clicks == 0


def test_set_switch_does_not_wait_when_change_is_gated():
    """二次确认型开关：状态要等后面那一下「确认」才变，堵在这里等必然超时。"""
    page, switch = _switch_page({"on": False})
    trace = _trace({
        "selector": {"sel": 'locator("#switch")'},
        "action": {
            "type": "SetSwitch",
            "param": {
                "state": True,
                "via": {"type": "aria", "gated": True, "gatedSteps": ["x-1"]},
            },
        },
    })

    execution = replay_trace(page, trace, timeout_ms=500)

    assert execution["status"] == "success"
    assert switch.clicks == 1


def test_set_switch_still_fails_when_state_never_arrives():
    """没标 gated 的开关，拨了却不动就是失败 —— 不能因为放宽而把它也放过。"""
    page, switch = _switch_page({"on": False})
    trace = _trace({
        "selector": {"sel": 'locator("#switch")'},
        "action": {
            "type": "SetSwitch",
            "param": {"state": True, "via": {"type": "aria"}},
        },
    })

    execution = replay_trace(page, trace, timeout_ms=300)

    assert execution["status"] == "failed"
    assert "开关未到达目标状态" in execution["steps"][0]["error"]


def test_late_overlay_reruns_the_skipped_optional_step():
    """晚出现的浮层：跳过 optional 只是暂缓，被挡住时要回来补做。

    首启弹窗常在页面加载好几秒后才弹出，而关它的那一步排在轨迹最前面。
    走到那一步时它还不在（optional，跳过），几秒后弹出来，遮罩把后面每一次
    点击都吞掉 —— 报出来是一串「点击超时」，看不出根因。
    """
    world = {"overlay": False, "closed": False, "looked": 0}

    class Closer:
        def wait_for(self, **kwargs):
            world["looked"] += 1
            if not world["overlay"]:
                raise TimeoutError("Locator.wait_for: Timeout")

        def count(self):
            return 1 if world["overlay"] else 0

        def click(self):
            world["closed"] = True
            world["overlay"] = False

    class Target:
        def wait_for(self, **kwargs):
            # 浮层是在第一步之后才弹出来的，弹一次
            if not world["closed"]:
                world["overlay"] = True

        def click(self):
            if world["overlay"]:
                raise TimeoutError(
                    "Locator.click: Timeout 5000ms exceeded.\n"
                    '  - <div class="overlay"></div> intercepts pointer events'
                )

    class Page:
        def goto(self, url):
            pass

        def locator(self, selector):
            return Closer() if "close" in selector else Target()

    trace = _trace({
        "selector": {"sel": 'locator("#close")'},
        "action": {"type": "Click", "param": {}},
        "optional": True,
    })
    trace["steps"]["step-0001"]["next"] = "step-0002"
    trace["steps"]["step-0002"] = {
        "status": "ready",
        "selector": {"sel": 'locator("#save")'},
        "action": {"type": "Click", "param": {}},
        "next": None,
    }

    execution = replay_trace(Page(), trace, timeout_ms=300)

    assert execution["status"] == "success", execution["steps"]
    assert world["closed"] is True, "没有回来补做那一步，浮层还挡着"
    first, second = execution["steps"]
    assert first["status"] == "skipped"
    assert first["laterPerformed"] is True
    assert second["status"] == "success"
    assert second["recoveredOptional"] == ["step-0001"]
    assert second["retries"] == 1, "补做和重试要算进 retries，效率分该扣"


def test_overlay_step_recorded_later_is_pulled_forward():
    """关浮层那一步排在后面：录制时弹窗是在这一下之后才弹的，回放时提前弹了。"""
    world = {"overlay": True, "closed": False}

    class Closer:
        def wait_for(self, **kwargs):
            if not world["overlay"]:
                raise TimeoutError("Locator.wait_for: Timeout")

        def count(self):
            return 1 if world["overlay"] else 0

        def click(self):
            world["closed"] = True
            world["overlay"] = False

    class Menu:
        def wait_for(self, **kwargs):
            pass

        def click(self):
            if world["overlay"]:
                raise TimeoutError(
                    "Locator.click: Timeout 300ms exceeded.\n"
                    '  - <div class="mask"></div> intercepts pointer events'
                )

    class Page:
        def goto(self, url):
            pass

        def locator(self, selector):
            return Closer() if "close" in selector else Menu()

    trace = _trace({
        "selector": {"sel": 'locator("#menu")'},
        "action": {"type": "Click", "param": {}},
    })
    trace["steps"]["step-0001"]["next"] = "step-0002"
    trace["steps"]["step-0002"] = {
        "status": "ready",
        "selector": {"sel": 'locator("#close")'},
        "action": {"type": "Click", "param": {}},
        "optional": True,
        "dismissesOverlay": True,
        "next": None,
    }

    execution = replay_trace(Page(), trace, timeout_ms=300)

    assert execution["status"] == "success", execution["steps"]
    assert world["closed"] is True
    first, second = execution["steps"]
    assert first["status"] == "success"
    assert first["recoveredOptional"] == ["step-0002"]
    assert second["status"] == "skipped"
    assert second["performedEarly"] is True


def test_only_overlay_steps_are_pulled_forward():
    """不是随便挑一步来试：只有录制时观察到「点完浮层就没了」的步骤才有资格。"""
    world = {"overlay": True, "touched": []}

    class Blocked:
        def wait_for(self, **kwargs):
            pass

        def click(self):
            raise TimeoutError(
                "Locator.click: Timeout 300ms exceeded.\n"
                '  - <div class="mask"></div> intercepts pointer events'
            )

    class Other:
        def wait_for(self, **kwargs):
            pass

        def click(self):
            world["touched"].append("later")

    class Page:
        def goto(self, url):
            pass

        def locator(self, selector):
            return Blocked() if "menu" in selector else Other()

    trace = _trace({
        "selector": {"sel": 'locator("#menu")'},
        "action": {"type": "Click", "param": {}},
    })
    trace["steps"]["step-0001"]["next"] = "step-0002"
    trace["steps"]["step-0002"] = {
        "status": "ready",
        "selector": {"sel": 'locator("#save")'},
        "action": {"type": "Click", "param": {}},
        "optional": True,          # 可选，但不是关浮层的
        "next": None,
    }

    execution = replay_trace(Page(), trace, timeout_ms=300)

    assert execution["status"] == "failed"
    assert world["touched"] == [], "把无关的后续步骤提前做了"


def test_intercepted_click_still_fails_when_nothing_to_recover():
    """没有暂缓步骤可补时，「点不动」照样是失败 —— 不能因为放宽而放过它。"""
    class Target:
        def wait_for(self, **kwargs):
            pass

        def click(self):
            raise TimeoutError(
                "Locator.click: Timeout 300ms exceeded.\n"
                '  - <div class="mask"></div> intercepts pointer events'
            )

    class Page:
        def goto(self, url):
            pass

        def locator(self, selector):
            return Target()

    trace = _trace({
        "selector": {"sel": 'locator("#save")'},
        "action": {"type": "Click", "param": {}},
    })

    execution = replay_trace(Page(), trace, timeout_ms=300)

    assert execution["status"] == "failed"
    assert "intercepts pointer events" in execution["steps"][0]["error"]


def test_evaluation_rejects_reversed_golden_path():
    golden = _network_trace()
    golden["steps"]["step-0001"]["next"] = "step-0002"
    golden["steps"]["step-0002"] = {
        "status": "ready",
        "selector": {"sel": 'locator("#done")'},
        "action": {
            "type": "Assert",
            "param": {"assertion": "visible", "expected": True},
        },
        "next": None,
    }
    execution = {
        "schema": "edr.execution-trace/v1",
        "goldenSchema": "edr.success-trace/v2",
        "status": "success",
        "steps": [
            {"nodeId": "step-0002", "status": "success", "actualAction": "Assert"},
            {"nodeId": "step-0001", "status": "success", "actualAction": "Click",
             "responses": [{"ok": True}]},
        ],
    }

    report = evaluate_trace(golden, execution)

    assert report["taskSuccess"] is False
    assert report["trajectoryOrderRate"] == 0
    assert report["score"] < 100


def test_evaluation_penalizes_extra_actions_and_retries():
    execution = {
        "schema": "edr.execution-trace/v1",
        "goldenSchema": "edr.success-trace/v2",
        "status": "success",
        "steps": [
            {"nodeId": "unplanned", "status": "success", "actualAction": "Click"},
            {"nodeId": "step-0001", "status": "success", "actualAction": "Click",
             "responses": [{"ok": True}], "retries": 1},
        ],
    }

    report = evaluate_trace(_network_trace(), execution)

    assert report["taskSuccess"] is True
    assert report["extraActionCount"] == 1
    assert report["retryCount"] == 1
    assert report["trajectoryEfficiency"] < 1
    assert report["score"] < 100


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


def test_visual_only_retries_until_async_ui_matches(monkeypatch, tmp_path):
    template = _pattern()
    asset_dir = tmp_path / "flow.assets"
    asset_dir.mkdir()
    cv2.imwrite(str(asset_dir / "step-0001.element.png"), template)
    loading = np.full((90, 140, 3), 248, dtype=np.uint8)
    ready = loading.copy()
    ready[30:46, 50:70] = template

    class Mouse:
        clicked = []

        def click(self, x, y, **kwargs):
            self.clicked.append((x, y, kwargs))

    class Page:
        mouse = Mouse()
        frames = [loading, ready]

        def goto(self, url):
            pass

        def screenshot(self):
            return _png(self.frames.pop(0) if len(self.frames) > 1 else self.frames[0])

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
        "action": {"type": "Click", "param": {}},
    })
    monkeypatch.setattr("replay_trace.time.sleep", lambda _: None)

    execution = replay_trace(
        Page(), trace, template_root=tmp_path, targeting="visual_only"
    )

    assert execution["status"] == "success"
    assert len(Page.mouse.clicked) == 1
    assert Page.frames == [ready]


def test_visual_only_uses_dom_verifier_for_manual_hidden_assertion():
    class HiddenLocator:
        def wait_for(self, **kwargs):
            raise AssertionError("断言不能预先等待元素可见")

        def is_visible(self):
            return False

    class Page:
        def goto(self, url):
            assert url == "https://app.example/form"

        def locator(self, selector):
            assert selector == "#dialog"
            return HiddenLocator()

    trace = _trace({
        "selector": {"sel": 'locator("#dialog")'},
        "action": {
            "type": "Assert",
            "param": {"assertion": "visible", "expected": False},
        },
    })

    execution = replay_trace(Page(), trace, targeting="visual_only")

    assert execution["status"] == "success"
    assert execution["steps"][0]["target"] == {"mode": "verifier"}
    assert evaluate_trace(trace, execution)["taskSuccess"] is True


def test_manual_assertion_retries_until_ui_reaches_expected_state():
    class EventuallyVisible:
        reads = 0

        def is_visible(self):
            self.reads += 1
            return self.reads >= 2

    locator = EventuallyVisible()

    class Page:
        def goto(self, url):
            pass

        def locator(self, selector):
            return locator

    trace = _trace({
        "selector": {"sel": 'locator("#saved")'},
        "action": {
            "type": "Assert",
            "param": {"assertion": "visible", "expected": True},
        },
    })

    execution = replay_trace(Page(), trace, targeting="visual_only", timeout_ms=500)

    assert execution["status"] == "success"
    assert locator.reads == 2


def test_manual_text_assertion_uses_playwright_whitespace_semantics():
    class Locator:
        def inner_text(self):
            return "  Saved\n   successfully  "

    class Page:
        def goto(self, url):
            pass

        def locator(self, selector):
            return Locator()

    trace = _trace({
        "selector": {"sel": 'locator("#status")'},
        "action": {
            "type": "Assert",
            "param": {"assertion": "text", "expected": "Saved successfully"},
        },
    })

    execution = replay_trace(Page(), trace, timeout_ms=100)

    assert execution["status"] == "success"


def test_input_text_fails_when_required_secret_is_missing(monkeypatch):
    monkeypatch.setenv("REC_PASSWORD", "process-secret-must-not-leak")

    class Locator:
        def wait_for(self, **kwargs):
            pass

        def fill(self, value):
            raise AssertionError("凭据缺失时不得输入空字符串")

    class Page:
        def goto(self, url):
            pass

        def locator(self, selector):
            return Locator()

    trace = _trace({
        "selector": {"sel": 'locator("#password")'},
        "action": {
            "type": "InputText",
            "param": {"valueFromEnv": "REC_PASSWORD"},
        },
    })

    execution = replay_trace(Page(), trace, env={})

    assert execution["status"] == "failed"
    assert "REC_PASSWORD" in execution["steps"][0]["error"]


def test_evaluation_rejects_wrong_execution_schema():
    execution = {
        "schema": "unknown/v1",
        "goldenSchema": "edr.success-trace/v2",
        "status": "success",
        "steps": [],
    }

    with pytest.raises(TraceReplayError, match="execution schema"):
        evaluate_trace(_network_trace(), execution)


def test_evaluation_rejects_negative_retry_count():
    execution = {
        "schema": "edr.execution-trace/v1",
        "goldenSchema": "edr.success-trace/v2",
        "status": "success",
        "steps": [{
            "nodeId": "step-0001",
            "status": "success",
            "actualAction": "Click",
            "responses": [{"ok": True}],
            "retries": -1,
        }],
    }

    with pytest.raises(TraceReplayError, match="retries"):
        evaluate_trace(_network_trace(), execution)


def test_empty_golden_trace_rejects_extra_execution_actions():
    golden = {
        "schema": "edr.success-trace/v2",
        "name": "empty",
        "status": "ready",
        "entry": None,
        "steps": {},
    }
    execution = {
        "schema": "edr.execution-trace/v1",
        "goldenSchema": "edr.success-trace/v2",
        "status": "success",
        "steps": [{
            "nodeId": "unplanned",
            "status": "success",
            "actualAction": "Click",
        }],
    }

    report = evaluate_trace(golden, execution)

    assert report["taskSuccess"] is False
    assert report["trajectoryEfficiency"] == 0
    assert report["score"] < 100


def test_validate_trace_rejects_cycle():
    trace = _network_trace()
    trace["steps"]["step-0001"]["next"] = "step-0001"

    with pytest.raises(TraceReplayError, match="存在环"):
        validate_trace(trace)


# ── 可选步骤 ──────────────────────────────────────────────────────────
# 首启弹窗、提示条这类元素出现与否取决于账号状态和历史操作：录制时出现过，
# 回放时往往已经不在（第一次关掉后应用记住了）。generate_spec 早就把它们
# 生成为「存在则点」，轨迹这边必须有同样的语义，否则同一份录制两套行为。

def _optional_trace(optional=True):
    trace = _trace({
        "selector": {"kind": "css", "sel": 'locator("div.tip > span.close")'},
        "action": {"type": "Click", "param": {}},
    })
    trace["steps"]["step-0001"]["optional"] = optional
    trace["steps"]["step-0001"]["next"] = "step-0002"
    trace["steps"]["step-0002"] = {
        "status": "ready", "next": None,
        "selector": {"kind": "text", "sel": 'getByText("下一步", { exact: true })'},
        "action": {"type": "Click", "param": {}},
    }
    return trace


class _Mouse:
    def __init__(self, page):
        self.page = page

    def click(self, x, y, **kwargs):
        self.page.clicked.append(("mouse", x, y))


class _MissingThenPresentPage:
    """第一个元素找不到（可选弹窗没出现），第二个正常。"""

    def __init__(self):
        self.clicked = []
        self.mouse = _Mouse(self)

    def goto(self, url, **kwargs):
        pass

    def locator(self, selector, **kwargs):
        return _MissingLocator(self, selector) if "close" in selector else _OkLocator(self, selector)

    def get_by_text(self, text, **kwargs):
        return _OkLocator(self, text)


class _MissingLocator:
    """元素真的不在：count() 为 0。"""

    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    def count(self):
        return 0

    def wait_for(self, **kwargs):
        from playwright.sync_api import TimeoutError as PWTimeoutError
        raise PWTimeoutError("Timeout 5000ms exceeded")

    def click(self, **kwargs):
        raise AssertionError("不该点到没出现的可选元素")


class _OkLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    def count(self):
        return 1

    def wait_for(self, **kwargs):
        pass

    def click(self, **kwargs):
        self.page.clicked.append(self.selector)


def test_optional_step_missing_is_skipped_not_failed():
    page = _MissingThenPresentPage()

    execution = replay_trace(page, _optional_trace())

    assert [s["status"] for s in execution["steps"]] == ["skipped", "success"]
    assert execution["status"] == "success"
    assert page.clicked == ["下一步"]   # 可选步没点，必经步点了


def test_missing_step_without_optional_still_fails():
    """optional 必须是显式的 —— 否则它会变成万能挡箭牌，把真问题一起吞掉。"""
    page = _MissingThenPresentPage()

    execution = replay_trace(page, _optional_trace(optional=False))

    assert execution["steps"][0]["status"] == "failed"
    assert execution["status"] == "failed"


def test_generate_trace_keeps_css_business_clicks_required():
    """CSS 是定位手段，不是「这一步可以不做」的语义证据。"""
    from generate_trace import generate_trace

    steps = [
        {"id": "s1", "t": 1, "type": "click", "kind": "css",
         "sel": 'locator("canvas.editor")', "css": "canvas.editor"},
        {"id": "s2", "t": 2, "type": "click", "kind": "text",
         "sel": 'getByText("提交", { exact: true })', "label": "提交"},
    ]
    trace = generate_trace(steps, [], name="t", start_url="https://app.example/")

    import trace_schema as ts
    assert ts.is_optional(trace["step_0001"]) is False
    assert "optional" not in ts.provenance(trace["step_0002"])


def test_generate_trace_marks_only_proven_overlay_dismissal_optional():
    from generate_trace import generate_trace

    steps = [{
        "id": "s1", "t": 1, "type": "click", "kind": "css",
        "sel": 'locator("div.tip > span.close")', "dismissesOverlay": True,
    }]

    trace = generate_trace(steps, [], name="t", start_url="https://app.example/")

    import trace_schema as ts
    assert ts.is_optional(trace["step_0001"]) is True


def test_locator_walks_the_complete_nested_frame_chain():
    calls = []

    class Root:
        def frame_locator(self, selector):
            calls.append(selector)
            return self

        def locator(self, selector):
            calls.append(("target", selector))
            return "target"

    selector = {
        "sel": 'locator("#save")',
        "inFrame": True,
        "frameChain": [
            {"id": "outer", "src": "/shell.html", "index": 0},
            {"name": "editor", "src": "/editor.html", "index": 1},
        ],
    }

    assert _locator(Root(), selector) == "target"
    assert calls == [
        'iframe[id="outer"], frame[id="outer"]',
        'iframe[name="editor"], frame[name="editor"]',
        ("target", "#save"),
    ]


def test_node_page_guard_rejects_replay_on_the_wrong_route():
    class Page:
        url = "https://app.example/settings#users"

    node = node_to_v2({
        "selector": {"sel": 'locator("#save")'},
        "action": {"type": "Click", "param": {}},
        "pageUrl": "/policies#edit",
    })

    with pytest.raises(TraceReplayError, match="页面路径不符"):
        _assert_node_page(Page(), node)


def test_replay_selects_the_popup_that_matches_the_recorded_route():
    class Context:
        pages = []

    class Page:
        context = Context()

        def __init__(self, url):
            self.url = url

    opener = Page("https://app.example/home")
    popup = Page("https://app.example/editor?draft=1#canvas")
    Context.pages = [opener, popup]
    node = node_to_v2({
        "selector": {"sel": 'locator("#save")'},
        "action": {"type": "Click", "param": {}},
        "pageUrl": "/editor?draft=1#canvas",
    })

    assert _page_for_node(opener, node) is popup


# ── 起点校验 ──────────────────────────────────────────────────────────
# 登录态失效时页面会被踢到登录页，于是每一步 DOM 都找不到元素、视觉又匹配不到，
# 最终报「视觉匹配分数不足」—— 整条链路没有一处提到「你没登录」。
# 实测被这个坑带偏过一整轮，所以要在第一步就说清楚。

class _RedirectedPage(_NetworkPage):
    def __init__(self, landed):
        super().__init__()
        self._landed = landed

    def goto(self, url, **kwargs):
        super().goto(url)

    @property
    def url(self):
        return self._landed


def test_replay_reports_auth_redirect_instead_of_locator_noise():
    page = _RedirectedPage("https://app.example/unisso/login.action?service=%2Fform")

    with pytest.raises(TraceReplayError) as excinfo:
        replay_trace(page, _network_trace())

    message = str(excinfo.value)
    assert "登录态" in message          # 说出真正的原因
    assert "login.action" in message    # 并给出落到了哪里


def test_replay_tolerates_benign_redirect_with_warning():
    """尾斜杠、语言前缀这类正常跳转不该打断回放，只记一条警告。"""
    page = _RedirectedPage("https://app.example/zh-CN/form")

    execution = replay_trace(page, _network_trace())

    assert execution["status"] == "success"
    assert any("重定向" in w for w in execution.get("warnings", []))


def test_visual_fallback_keeps_the_dom_failure_reason(monkeypatch):
    """回退到视觉时，DOM 为什么失败必须留在执行记录里。

    否则事后翻 execution.json 只看得到「视觉匹配分数不足」，
    分不清是选择器写错了、元素本来就不该出现、还是整页都不对。
    """
    import replay_trace as rt

    class _Match:
        score = 0.99
        verify_score = 0.98
        scale = 1.0

    class _Visual:
        kind = "element"
        match = _Match()
        x, y = 10, 20

    monkeypatch.setattr(rt, "locate_visual_target", lambda *a, **k: _Visual())

    node = {
        "selector": {"kind": "css", "sel": 'locator("div.close-gone")'},
        "action": {"type": "Click", "param": {}},
        "recognition": {"type": "TemplateMatch", "templates": {"element": {}}},
    }
    page = _MissingThenPresentPage()
    execution = replay_trace(page, _trace(node))

    target = execution["steps"][0]["target"]
    assert target["mode"] == "visual"
    assert "TimeoutError" in target["domError"]


class _PresentButUnclickableLocator(_OkLocator):
    """元素在（count=1），但点不动 —— 典型是被遮罩挡住。"""

    def click(self, **kwargs):
        from playwright.sync_api import TimeoutError as PWTimeoutError
        raise PWTimeoutError("Locator.click: Timeout 20000ms exceeded")


class _BlockedPage(_MissingThenPresentPage):
    def locator(self, selector, **kwargs):
        return _PresentButUnclickableLocator(self, selector)


def test_optional_step_present_but_unclickable_still_fails():
    """可选 ≠ 出问题就放过。

    实测栽过：首启弹窗**确实在**，只是被判为视觉歧义，于是这一步被跳过 ——
    弹窗没关掉，遮罩把后面每一次点击都吞了，最后报的是莫名其妙的「点击超时」。
    测试里把失败当跳过，等于把问题推到一个报错不知所云的地方。
    """
    trace = _optional_trace()
    trace["steps"].pop("step-0002")
    trace["steps"]["step-0001"]["next"] = None

    execution = replay_trace(_BlockedPage(), trace)

    assert execution["steps"][0]["status"] == "failed"
    assert execution["status"] == "failed"


def test_visual_ambiguity_is_not_absence():
    """歧义说明目标存在，只是分不清是哪个 —— 不能当缺席跳过。"""
    from rec_visual import VisualAbsent, VisualAmbiguous
    from replay_trace import _target_absent

    node = {"selector": {}}          # 没有 DOM 依据，只能看视觉证据
    assert _target_absent(None, node_to_v2(node), VisualAbsent("分数不足")) is True
    assert _target_absent(None, node_to_v2(node), VisualAmbiguous("不唯一")) is False


# ── 读请求不必发 ──────────────────────────────────────────────────────
# 读请求只在状态真的变化时才重发。回放时页面若已处于目标状态，同样的点击
# 一个包都不发 —— 那一步其实是成功的（作用域已经对了），不该判失败。
# 实测栽过：点 default-group 时它已是选中态，list-group-asset 没重发，
# 整条轨迹卡在第 3 步。

def _read_only_trace(required):
    trace = _trace({
        "selector": {"kind": "text", "sel": 'getByText("组A", { exact: true })'},
        "action": {"type": "Click", "param": {}},
        "expect": {"responses": [{
            "method": "GET",
            "url": "https://app.example/api/list",
            "expectedStatus": 200,
            "required": required,
        }]},
    })
    return trace


class _SilentPage(_MissingThenPresentPage):
    """点击不产生任何请求 —— 页面已处于目标状态。"""

    def expect_response(self, predicate, timeout=None):
        page = self

        class _Waiter:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                from playwright.sync_api import TimeoutError as PWTimeoutError
                raise PWTimeoutError(f"Timeout {timeout}ms exceeded")

            @property
            def value(self):
                raise AssertionError("没有响应可读")

        return _Waiter()

    def get_by_text(self, text, **kwargs):
        return _OkLocator(self, text)


def test_read_response_may_be_absent_when_state_already_matches():
    execution = replay_trace(_SilentPage(), _read_only_trace(required=False))

    assert execution["steps"][0]["status"] == "success"
    assert execution["status"] == "success"
    note = execution["steps"][0]["responses"][0]
    assert note["ok"] is False and note["required"] is False


def test_write_response_absence_still_fails():
    """写请求必发 —— 没发出去就是没做成，不能因为宽容读请求把它一起放过。"""
    execution = replay_trace(_SilentPage(), _read_only_trace(required=True))

    assert execution["steps"][0]["status"] == "failed"
    assert execution["status"] == "failed"


def test_absent_read_response_does_not_drag_down_the_score():
    golden = _read_only_trace(required=False)
    execution = replay_trace(_SilentPage(), golden)

    report = evaluate_trace(golden, execution)
    assert report["networkAssertionRate"] == 1.0   # 分母里本就不该有它
    assert report["taskSuccess"] is True


def test_ambiguity_beats_a_stale_dom_selector():
    """「存在」的证据优先于「查不到」。

    实测栽过：弹窗明明在（视觉 best=0.976，只是有两个长得一样的），
    但那条 CSS 路径匹配不到 → DOM 命中 0 → 被判缺席跳过 → 遮罩没关掉，
    下一步点击被吞、20 秒超时。DOM 命中 0 只说明选择器失效，不说明元素不在。
    """
    from rec_visual import VisualAbsent, VisualAmbiguous
    from replay_trace import _target_absent

    class _ZeroHit:
        def count(self):
            return 0

    class _Page:
        def locator(self, *a, **k):
            return _ZeroHit()

    node = {"selector": {"kind": "css", "sel": 'locator("div.stale")'}}
    assert _target_absent(_Page(), node_to_v2(node), VisualAmbiguous("不唯一")) is False
    assert _target_absent(_Page(), node_to_v2(node), VisualAbsent("分数不足")) is True
