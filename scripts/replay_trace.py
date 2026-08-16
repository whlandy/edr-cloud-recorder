"""Replay a golden E2E trace and evaluate the resulting agent execution trace."""

from __future__ import annotations

import base64
import json
import os
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from rec_assert import ANY_STR, assert_subset
from rec_secrets import REDACTED
from rec_visual import VisualMatchError, locate_visual_target
from selector_py import to_python


TRACE_SCHEMA = "edr.success-trace/v1"
EXECUTION_SCHEMA = "edr.execution-trace/v1"
SUPPORTED_ACTIONS = {
    "Click", "DoubleClick", "InputText", "Check", "Uncheck",
    "SetSwitch", "PressKey", "Assert",
}


class TraceReplayError(RuntimeError):
    pass


def load_trace(path: str | Path) -> dict:
    trace = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_trace(trace)
    return trace


def validate_trace(trace: dict) -> list[str]:
    if trace.get("schema") != TRACE_SCHEMA:
        raise TraceReplayError(f"不支持的 trace schema: {trace.get('schema')!r}")
    steps = trace.get("steps")
    if not isinstance(steps, dict):
        raise TraceReplayError("trace.steps 必须是对象")
    entry = trace.get("entry")
    if not steps:
        if entry is not None:
            raise TraceReplayError("空 trace 的 entry 必须是 null")
        return []
    if entry not in steps:
        raise TraceReplayError(f"trace.entry 不存在: {entry!r}")

    order, seen, current = [], set(), entry
    while current is not None:
        if current in seen:
            raise TraceReplayError(f"trace 存在环: {current}")
        if current not in steps:
            raise TraceReplayError(f"next 指向不存在的步骤: {current}")
        seen.add(current)
        order.append(current)
        action = (steps[current].get("action") or {}).get("type")
        if action not in SUPPORTED_ACTIONS:
            raise TraceReplayError(f"{current} 使用不支持的动作: {action!r}")
        current = steps[current].get("next")
    unreachable = set(steps) - seen
    if unreachable:
        raise TraceReplayError(f"trace 包含不可达步骤: {sorted(unreachable)}")
    return order


def _locator(page, selector: dict):
    source = selector.get("sel")
    if not source:
        return None
    root = page
    if selector.get("inFrame") and selector.get("framePath"):
        tail = selector["framePath"].split("/")[-1]
        root = page.frame_locator(f'iframe[src*="{tail}"]')
    expression = "root." + to_python(source)
    return eval(expression, {"__builtins__": {}}, {"root": root})


def _visual_ui(node: dict) -> dict:
    recognition = node.get("recognition") or {}
    relative = (node.get("action") or {}).get("param", {}).get("relativePoint") or {}
    return {
        **(node.get("geometry") or {}),
        "templates": recognition.get("templates") or {},
        "click": {"rx": relative.get("x", 0.5), "ry": relative.get("y", 0.5)},
    }


def _target(page, node: dict, template_root: Path, targeting: str, timeout_ms: int,
            focused_selector: str | None):
    action_type = (node.get("action") or {}).get("type")
    if action_type == "Assert":
        locator = _locator(page, node.get("selector") or {})
        if locator is None:
            raise TraceReplayError("断言步骤缺少 DOM 选择器")
        return "verifier", locator, None
    if action_type == "PressKey" and targeting == "visual_only":
        selector = (node.get("selector") or {}).get("sel")
        if not selector or selector != focused_selector:
            raise TraceReplayError("视觉按键步骤没有同目标动作建立的可信焦点")
        return "keyboard", None, None

    locator = None
    if targeting != "visual_only":
        locator = _locator(page, node.get("selector") or {})
    if locator is not None:
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return "dom", locator, None
        except Exception:
            if not node.get("recognition"):
                raise
    if not node.get("recognition"):
        raise TraceReplayError("DOM 定位失败且步骤没有视觉模板")
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            visual = locate_visual_target(
                page, template_root=template_root, ui=_visual_ui(node)
            )
            break
        except VisualMatchError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    return "visual", None, visual


def _response_predicate(expected: dict, occurrence: int):
    seen = [0]
    expected_url = urlsplit(expected["url"])

    def predicate(response):
        actual = urlsplit(response.url)
        same_url = (
            response.url == expected["url"]
            or (actual.path, actual.query) == (expected_url.path, expected_url.query)
        )
        if response.request.method == expected["method"] and same_url:
            seen[0] += 1
            return seen[0] == occurrence
        return False

    return predicate


def _response_body(response):
    try:
        return response.text()
    except Exception:
        return ""


def _request_body(request):
    try:
        value = request.post_data_json
        return value() if callable(value) else value
    except Exception:
        return request.post_data


def _request_body_base64(request) -> str:
    value = request.post_data_buffer
    data = value() if callable(value) else value
    return base64.b64encode(data or b"").decode("ascii")


def _redacted_matchers(value):
    if value == REDACTED:
        return ANY_STR
    if isinstance(value, dict):
        return {key: _redacted_matchers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted_matchers(item) for item in value]
    return value


def _validate_response(response, expected: dict) -> dict:
    actual = {
        "method": response.request.method,
        "url": response.url,
        "status": response.status,
        "ok": False,
    }
    if response.status != expected["expectedStatus"]:
        raise AssertionError(
            f"响应状态不符: {response.status} != {expected['expectedStatus']}"
        )
    expected_request = expected.get("request") or {}
    if "body" in expected_request:
        assert_subset(
            _request_body(response.request),
            _redacted_matchers(expected_request["body"]),
        )
    if "bodyBase64" in expected_request:
        actual_body = _request_body_base64(response.request)
        if actual_body != expected_request["bodyBase64"]:
            raise AssertionError("二进制请求体与成功 trace 不一致")
    if "expectedBody" in expected:
        actual_body, expected_body = _response_body(response), expected["expectedBody"]
        try:
            assert_subset(
                json.loads(actual_body),
                _redacted_matchers(json.loads(expected_body)),
            )
        except (TypeError, ValueError):
            if REDACTED not in expected_body and actual_body != expected_body:
                raise AssertionError("响应体与成功 trace 不一致")
    actual["ok"] = True
    return actual


def _assert_locator(locator, param: dict, timeout_ms: int) -> None:
    assertion, expected = param.get("assertion"), param.get("expected")
    readers = {
        "visible": lambda: locator.is_visible(),
        "text": lambda: locator.inner_text(),
        "value": lambda: locator.input_value(),
        "checked": lambda: locator.is_checked(),
        "attribute": lambda: locator.get_attribute(param.get("attribute")),
    }
    if assertion not in readers:
        raise TraceReplayError(f"不支持的断言: {assertion!r}")

    deadline = time.monotonic() + timeout_ms / 1000
    actual = None
    while True:
        try:
            actual = readers[assertion]()
            matches = actual == expected
            if assertion == "text" and isinstance(actual, str) and isinstance(expected, str):
                matches = " ".join(actual.split()) == " ".join(expected.split())
            if matches:
                return
        except Exception as error:
            actual = f"{type(error).__name__}: {error}"
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"断言失败: {assertion} actual={actual!r} expected={expected!r}"
            )
        time.sleep(0.1)


def _switch_reader(locator, via: dict):
    state_target = locator.locator(via["within"]).first if via.get("within") else locator
    if via.get("type") == "checked":
        return state_target.is_checked
    if via.get("type") == "class":
        token = via.get("token")
        def read_class():
            present = state_target.evaluate(
                "(e, token) => e.classList.contains(token)", token
            )
            return not present if via.get("polarity") == "off" else present
        return read_class
    return lambda: state_target.get_attribute("aria-checked") == "true"


def _set_switch(locator, param: dict, timeout_ms: int) -> None:
    desired = bool(param.get("state"))
    read_state = _switch_reader(locator, param.get("via") or {})
    if read_state() == desired:
        return
    locator.click()
    deadline = time.monotonic() + timeout_ms / 1000
    while read_state() != desired:
        if time.monotonic() >= deadline:
            raise AssertionError(f"开关未到达目标状态: expected={desired}")
        time.sleep(0.1)


def _execute_action(page, node: dict, template_root: Path, targeting: str,
                    timeout_ms: int, env: dict,
                    focused_selector: str | None) -> dict:
    action = node["action"]
    action_type, param = action["type"], action.get("param") or {}
    mode, locator, visual = _target(
        page, node, template_root, targeting, timeout_ms, focused_selector
    )
    target_info = {"mode": mode}
    if visual is not None:
        target_info.update(
            templateKind=visual.kind,
            matchScore=visual.match.score,
            verifyScore=visual.match.verify_score,
            scale=visual.match.scale,
            point={"x": visual.x, "y": visual.y},
        )

    if action_type == "Assert":
        _assert_locator(locator, param, timeout_ms)
    elif action_type == "PressKey":
        (locator or page.keyboard).press(param.get("key", "Enter"))
    elif action_type == "InputText":
        source = param.get("valueFromEnv")
        if source:
            text = env.get(source)
            if not text:
                raise TraceReplayError(f"缺少输入所需的环境变量 {source}")
        else:
            text = param.get("text", "")
        if locator is not None:
            locator.fill(text)
        else:
            page.mouse.click(visual.x, visual.y)
            page.keyboard.press("ControlOrMeta+A")
            page.keyboard.insert_text(text)
    elif action_type == "SetSwitch" and locator is not None:
        _set_switch(locator, param, timeout_ms)
    elif action_type in {"Click", "DoubleClick", "Check", "Uncheck", "SetSwitch"}:
        if locator is not None:
            method = {
                "Click": "click", "DoubleClick": "dblclick",
                "Check": "check", "Uncheck": "uncheck", "SetSwitch": "click",
            }[action_type]
            getattr(locator, method)()
        else:
            kwargs = {"click_count": 2} if action_type == "DoubleClick" else {}
            page.mouse.click(visual.x, visual.y, **kwargs)
    else:
        raise TraceReplayError(f"不支持的动作: {action_type}")
    return target_info


def replay_trace(page, trace: dict | str | Path, *, template_root: str | Path | None = None,
                 targeting: str = "dom_first", timeout_ms: int = 5_000,
                 env: dict | None = None, execution_path: str | Path | None = None,
                 navigate: bool = True, raise_on_error: bool = False) -> dict:
    if targeting not in {"dom_first", "visual_only"}:
        raise ValueError("targeting 必须是 dom_first 或 visual_only")
    trace_path = Path(trace) if isinstance(trace, (str, Path)) else None
    golden = load_trace(trace_path) if trace_path else trace
    order = validate_trace(golden)
    if golden.get("status") != "ready":
        raise TraceReplayError("trace 尚未 ready，存在缺失模板的定位步骤")
    root = Path(template_root) if template_root else (
        trace_path.parent if trace_path else Path.cwd()
    )
    execution = {
        "schema": EXECUTION_SCHEMA,
        "goldenSchema": golden["schema"],
        "name": golden.get("name"),
        "startedAt": datetime.now().isoformat(),
        "status": "running",
        "steps": [],
    }
    runtime_env = os.environ if env is None else env
    if navigate and golden.get("startUrl"):
        try:
            page.goto(golden["startUrl"])
        except Exception as error:
            raise TraceReplayError(f"无法进入 trace 起始页面: {error}") from error
    first_error = None
    focused_selector = None
    for node_id in order:
        node = golden["steps"][node_id]
        started = time.perf_counter()
        result = {
            "nodeId": node_id,
            "expectedAction": node["action"]["type"],
            "actualAction": node["action"]["type"],
            "status": "running",
            "responses": [],
            "retries": 0,
        }
        try:
            expected_responses = (node.get("expect") or {}).get("responses") or []
            occurrences: dict[tuple[str, str], int] = {}
            with ExitStack() as stack:
                response_infos = []
                for expected in expected_responses:
                    key = (expected["method"], expected["url"])
                    occurrences[key] = occurrences.get(key, 0) + 1
                    response_infos.append(stack.enter_context(page.expect_response(
                        _response_predicate(expected, occurrences[key]), timeout=timeout_ms
                    )))
                result["target"] = _execute_action(
                    page, node, root, targeting, timeout_ms, runtime_env,
                    focused_selector,
                )
            for info, expected in zip(response_infos, expected_responses):
                result["responses"].append(_validate_response(info.value, expected))
            result["status"] = "success"
            if targeting == "visual_only" and result["target"]["mode"] == "visual":
                focused_selector = (node.get("selector") or {}).get("sel")
        except Exception as error:
            result["status"] = "failed"
            result["error"] = f"{type(error).__name__}: {error}"
            first_error = error
        finally:
            result["durationMs"] = round((time.perf_counter() - started) * 1000, 3)
            execution["steps"].append(result)
        if first_error:
            break
    execution["status"] = "failed" if first_error else "success"
    execution["finishedAt"] = datetime.now().isoformat()
    if execution_path:
        Path(execution_path).write_text(
            json.dumps(execution, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if first_error and raise_on_error:
        raise TraceReplayError(str(first_error)) from first_error
    return execution


def evaluate_trace(golden: dict, execution: dict) -> dict:
    order = validate_trace(golden)
    if execution.get("schema") != EXECUTION_SCHEMA:
        raise TraceReplayError(
            f"不支持的 execution schema: {execution.get('schema')!r}"
        )
    if execution.get("goldenSchema") != golden["schema"]:
        raise TraceReplayError("execution trace 与黄金 trace schema 不一致")
    execution_steps = execution.get("steps")
    if not isinstance(execution_steps, list):
        raise TraceReplayError("execution.steps 必须是数组")

    actual = {}
    for step in execution_steps:
        if not isinstance(step, dict) or not step.get("nodeId"):
            raise TraceReplayError("execution step 缺少 nodeId")
        retries = step.get("retries", 0)
        if type(retries) is not int or retries < 0:
            raise TraceReplayError("execution step 的 retries 必须是非负整数")
        actual.setdefault(step["nodeId"], step)
    completed = sum(actual.get(node, {}).get("status") == "success" for node in order)
    action_hits = sum(
        actual.get(node, {}).get("actualAction") == golden["steps"][node]["action"]["type"]
        for node in order if node in actual
    )
    expected_network = sum(
        len((golden["steps"][node].get("expect") or {}).get("responses") or [])
        for node in order
    )
    network_hits = 0
    for node in order:
        expected_count = len(
            (golden["steps"][node].get("expect") or {}).get("responses") or []
        )
        responses = actual.get(node, {}).get("responses", [])
        network_hits += min(expected_count, sum(bool(item.get("ok")) for item in responses))
    visual_scores = [
        step["target"]["matchScore"]
        for step in execution_steps
        if step.get("target", {}).get("mode") == "visual"
    ]
    total = len(order)
    observed_path = [
        step["nodeId"]
        for step in execution_steps
        if step["nodeId"] in golden["steps"]
    ]
    order_hits = sum(
        expected == observed for expected, observed in zip(order, observed_path)
    )
    order_rate = (
        order_hits / max(total, len(observed_path))
        if total or observed_path else 1.0
    )
    extra_actions = max(0, len(execution_steps) - total)
    retries = sum(step.get("retries", 0) for step in execution_steps)
    efficiency = (
        total / (total + extra_actions + retries)
        if total else float(not execution_steps)
    )
    completion_rate = completed / total if total else 1.0
    action_accuracy = action_hits / total if total else 1.0
    network_rate = network_hits / expected_network if expected_network else 1.0
    task_success = (
        execution.get("status") == "success"
        and completed == total
        and observed_path == order
        and (bool(total) or not execution_steps)
    )
    score = 100 * (
        0.45 * float(task_success)
        + 0.20 * completion_rate
        + 0.10 * action_accuracy
        + 0.10 * network_rate
        + 0.10 * order_rate
        + 0.05 * efficiency
    )
    return {
        "taskSuccess": task_success,
        "score": round(score, 2),
        "stepCompletionRate": round(completion_rate, 4),
        "actionAccuracy": round(action_accuracy, 4),
        "networkAssertionRate": round(network_rate, 4),
        "trajectoryOrderRate": round(order_rate, 4),
        "trajectoryEfficiency": round(efficiency, 4),
        "extraActionCount": extra_actions,
        "retryCount": retries,
        "averageVisualMatchScore": (
            round(sum(visual_scores) / len(visual_scores), 4) if visual_scores else None
        ),
    }
