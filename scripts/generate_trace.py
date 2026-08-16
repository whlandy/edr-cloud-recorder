"""Compile one recorded E2E script into one Maa-style successful trace."""

from __future__ import annotations

import json
from typing import Any

from rec_visual import (
    AMBIGUITY_MARGIN,
    MATCH_THRESHOLD,
    SCALE_FACTORS,
    VERIFY_THRESHOLD,
)
from generate_spec import pair_network_events, prepare_steps
from rec_secrets import redact_sensitive_values


TRACE_SCHEMA = "edr.success-trace/v1"
POSITIONAL_STEP_TYPES = frozenset({
    "click", "dblclick", "check", "uncheck", "switch",
})


def _selector(step: dict) -> dict:
    return {
        key: step[key]
        for key in ("kind", "sel", "css", "label", "inFrame", "framePath")
        if step.get(key) is not None
    }


def _recognition(ui: dict) -> dict | None:
    templates = ui.get("templates") or {}
    order = [kind for kind in ("context", "element") if kind in templates]
    if not order:
        return None
    return {
        "type": "TemplateMatch",
        "templateOrder": order,
        "templates": templates,
        "threshold": MATCH_THRESHOLD,
        "ambiguityMargin": AMBIGUITY_MARGIN,
        "verifyThreshold": VERIFY_THRESHOLD,
        "scaleFactors": list(SCALE_FACTORS),
    }


def _action(step: dict, *, focus_before_input: bool = False) -> dict:
    step_type = step.get("type")
    action_type = {
        "click": "Click",
        "dblclick": "DoubleClick",
        "check": "Check",
        "uncheck": "Uncheck",
        "switch": "SetSwitch",
        "fill": "InputText",
        "press": "PressKey",
        "assert": "Assert",
    }.get(step_type, "RecordedAction")
    param: dict[str, Any] = {}
    if step_type in POSITIONAL_STEP_TYPES:
        click = (step.get("ui") or {}).get("click") or {}
        param["relativePoint"] = {
            "x": click.get("rx", 0.5),
            "y": click.get("ry", 0.5),
        }
        if step_type == "switch":
            param.update(state=bool(step.get("to")), via=step.get("via") or {})
    elif step_type == "fill":
        if step.get("secret"):
            param["valueFromEnv"] = "REC_PASSWORD"
        else:
            param["text"] = step.get("value", "")
        if focus_before_input:
            param["focusBeforeInput"] = True
    elif step_type == "press":
        param["key"] = step.get("key", "Enter")
    elif step_type == "assert":
        param.update(
            assertion=step.get("assertion"),
            expected=step.get("expected"),
        )
        if step.get("attribute") is not None:
            param["attribute"] = step["attribute"]
    else:
        param["recordedType"] = step_type
    return {"type": action_type, "param": param}


def _same_target(left: dict, right: dict) -> bool:
    return any(
        left.get(key) and left.get(key) == right.get(key)
        for key in ("css", "sel")
    )


def _fold_input_focus(steps: list[dict]) -> list[dict]:
    folded = []
    for step in steps:
        if (
            step.get("type") == "fill"
            and folded
            and folded[-1].get("type") == "click"
            and _same_target(folded[-1], step)
        ):
            focus = folded.pop()
            folded.append({
                **step,
                "t": focus.get("t", step.get("t")),
                "actionT": focus.get("actionT", focus.get("t")),
                "_focusUi": focus.get("ui") or {},
                "_sourceStepIds": [focus.get("id"), step.get("id")],
            })
        else:
            folded.append(step)
    return folded


def _request_payload(request: dict | None) -> dict | None:
    if not request:
        return None
    payload = {
        key: request[key]
        for key in ("body", "bodyBase64", "bodyEncoding")
        if request.get(key) is not None
    }
    if isinstance(payload.get("body"), str):
        try:
            payload["body"] = json.loads(payload["body"])
        except (TypeError, ValueError):
            pass
    if "body" in payload:
        payload["body"] = redact_sensitive_values(payload["body"])
    return payload or None


def _network_expectation(request: dict | None, response: dict) -> dict:
    param = {
        "method": response["method"],
        "url": response["url"],
        "expectedStatus": response["status"],
    }
    payload = _request_payload(request)
    if payload:
        param["request"] = payload
    if response.get("body") is not None:
        param["expectedBody"] = response["body"]
    return param


def generate_trace(steps: list[dict], net: list[dict] | None = None, *,
                   name: str, start_url: str) -> dict[str, Any]:
    _, steps = prepare_steps(steps)
    steps = _fold_input_focus(steps)
    network_pairs = pair_network_events(net or [])
    linear_nodes = []

    for index, step in enumerate(steps, 1):
        own_ui = step.get("ui") or {}
        visual_ui = step.get("_focusUi") or own_ui

        recognition = _recognition(visual_ui)
        focus_before_input = bool(step.get("_focusUi") and recognition)
        needs_template = (
            step.get("type") in POSITIONAL_STEP_TYPES or "_focusUi" in step
        )
        node = {
            "sourceStepId": step.get("id"),
            "timestampMs": step.get("t"),
            "status": "missing_template" if needs_template and not recognition else "ready",
            "selector": _selector(step),
            "action": _action(step, focus_before_input=focus_before_input),
        }
        if recognition:
            node["recognition"] = recognition
            node["geometry"] = {
                key: visual_ui[key]
                for key in ("pageRect", "pageViewport", "viewport", "deviceScaleFactor")
                if visual_ui.get(key) is not None
            }
        if step.get("_sourceStepIds"):
            node["sourceStepIds"] = step["_sourceStepIds"]
        high = steps[index].get("t", float("inf")) if index < len(steps) else float("inf")
        responses = []
        for request, response in network_pairs:
            trigger_t = (request or response).get("t", 0)
            if step.get("t", 0) <= trigger_t < high:
                responses.append(_network_expectation(request, response))
        if responses:
            node["expect"] = {"responses": responses}
        linear_nodes.append(node)

    nodes = {}
    for index, node in enumerate(linear_nodes, 1):
        node_id = f"step-{index:04d}"
        node["next"] = (
            f"step-{index + 1:04d}" if index < len(linear_nodes) else None
        )
        nodes[node_id] = node

    return {
        "schema": TRACE_SCHEMA,
        "name": name,
        "startUrl": start_url,
        "status": (
            "incomplete"
            if any(node["status"] == "missing_template" for node in nodes.values())
            else "ready"
        ),
        "entry": next(iter(nodes), None),
        "steps": nodes,
    }
