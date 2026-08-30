"""桌面轨迹 → v2 的自检。

守的是一件事：**动作不能在转换中悄悄消失**。
edr-wd 的原生轨迹直接喂给 maa-fw 时就是这么坏的 —— 每个节点都变成
DirectHit + DoNothing，两边都不报错。所以这里每条测试都盯着「转换之后它还是
原来那件事吗」，而不是「转换有没有抛异常」。
"""

import json
from pathlib import Path

import pytest

import trace_schema as ts
from desktop_to_v2 import DESKTOP_SCHEMA, DesktopTraceError, convert

EDR_WD = Path.home() / "ai-projects" / "edr-wd"


def _desktop(*steps):
    """拼一条桌面黄金轨迹。步骤按给定顺序串成 next 链。"""
    ids = [f"step-{i:04d}" for i in range(1, len(steps) + 1)]
    table = {}
    for index, (step_id, step) in enumerate(zip(ids, steps)):
        table[step_id] = {
            "stepId": step_id, "actionId": None, "args": {}, "selector": None,
            "endSelector": None, "verifiers": [], "required": True,
            "status": "ready", "issues": [],
            "next": ids[index + 1] if index + 1 < len(ids) else None,
            **step,
        }
    return {"schema": DESKTOP_SCHEMA, "status": "ready", "name": "case",
            "sourceRecording": {}, "catalog": {}, "environment": {},
            "entry": ids[0], "steps": table, "cleanup": []}


def _click(**extra):
    return {"actionId": "gui.click",
            "selector": {"control": {"automationId": "Win.btn", "name": "确定",
                                     "controlType": "Button", "fingerprint": "sha256:x",
                                     "ancestry": []},
                         "window": {"processName": "EDRClient.exe"}, "visual": None},
            **extra}


def test_click_survives_the_conversion():
    """转换前后都得是「点一下」。变成 DoNothing 就是静默丢失。"""
    trace = convert(_desktop(_click()))
    node = trace["step_0001"]
    assert node["action"]["type"] == "Click"


def test_next_is_a_list_of_node_ids():
    """写成字符串的话 maa-fw 会按字符迭代：'step-0002' → ['s','t','e','p',…]。"""
    trace = convert(_desktop(_click(), _click()))
    assert trace["step_0001"]["next"] == ["step_0002"]
    assert trace["step_0002"]["next"] == []


def test_node_ids_use_the_charset_maa_fw_normalises_to():
    """maa-fw 的 node_name() 把名字规范到 [0-9A-Za-z_]，连字符会被改写。"""
    trace = convert(_desktop(_click()))
    assert "step_0001" in trace and "step-0001" not in trace


def test_scroll_keeps_direction_and_amount():
    trace = convert(_desktop({**_click(), "actionId": "pointer.scroll",
                              "args": {"clicks": -27}}))
    param = trace["step_0001"]["action"]["param"]
    assert param == {"direction": "up", "clicks": 27}


def test_drag_is_marked_because_maa_fw_cannot_do_it():
    """maa-fw 的编译器对拖拽返回 None。保留语义并标出来 ——
    悄悄换成点击或 DoNothing 会让回放做另一件事而且不报错。"""
    trace = convert(_desktop({**_click(), "actionId": "pointer.drag"}))
    node = trace["step_0001"]
    assert node["action"]["type"] == "Drag"
    assert ts.provenance(node)["actionScope"] == ts.VERIFY_SCOPE_DESKTOP


def test_unknown_action_is_not_guessed():
    """没见过的动作不猜。猜错的后果是回放做了另一件事，而且不报错。"""
    trace = convert(_desktop({**_click(), "actionId": "gui.summon_dragon"}))
    node = trace["step_0001"]
    assert node["action"]["type"] == "DoNothing"
    assert node["action"]["param"]["unmappedAction"] == "gui.summon_dragon"
    assert ts.provenance(node)["actionScope"] == ts.VERIFY_SCOPE_DESKTOP


def test_text_assertion_becomes_ocr_maa_fw_can_verify():
    """文本断言是 OCR 的主场，别浪费成 DirectHit。"""
    trace = convert(_desktop({
        "actionId": None,
        "verifiers": [{"type": "text_equals", "expected": "管理员", "timeoutSeconds": 10.0}]}))
    node = trace["step_0001"]
    assert node["recognition"]["type"] == "OCR"
    assert node["recognition"]["param"]["expected"] == ["管理员"]


def test_desktop_only_verifiers_are_marked():
    """窗口是否打开、控件是否勾选 —— maa-fw 没有对应能力。
    不标的话它会以为验过了。"""
    trace = convert(_desktop({
        "actionId": None,
        "verifiers": [{"type": "checked", "expected": True}]}))
    assert ts.verification(trace["step_0001"])["scope"] == ts.VERIFY_SCOPE_DESKTOP


def test_templates_carry_over_with_their_order():
    trace = convert(_desktop({**_click(), "selector": {
        "control": {"automationId": "Win.btn", "name": "", "controlType": "Button",
                    "fingerprint": "", "ancestry": []},
        "window": {}, "visual": {"template": "assets/a-element.png",
                                 "contextTemplate": "assets/a-context.png",
                                 "relativePoint": [0.25, 0.75]}}}))
    node = trace["step_0001"]
    assert node["recognition"]["type"] == "TemplateMatch"
    assert ts.template_order(node) == ["context", "element"]
    assert node["action"]["param"]["target_ratio"] == [0.25, 0.75]


def test_optional_steps_survive():
    trace = convert(_desktop({**_click(), "required": False}))
    assert ts.is_optional(trace["step_0001"]) is True


def test_a_web_trace_is_refused():
    """喂错类型要响亮地失败，不能默默产出一条空轨迹。"""
    with pytest.raises(DesktopTraceError):
        convert({"schema": "edr.success-trace/v2", "steps": {}})


@pytest.mark.skipif(not (EDR_WD / "recordings").is_dir(), reason="本机没有 edr-wd 录制")
def test_real_edr_wd_recordings_convert_and_keep_their_actions():
    """真实语料：转换后不能出现「整条轨迹全是 DoNothing」这种静默丢失。"""
    for case in sorted((EDR_WD / "recordings").glob("*/golden-trace.json")):
        trace = convert(json.loads(case.read_text(encoding="utf-8")))
        actions = [node["action"]["type"] for _, node in ts.nodes(trace)]
        source_actions = [
            s.get("actionId") for s in
            json.loads(case.read_text(encoding="utf-8"))["steps"].values()]
        expected_real = sum(1 for a in source_actions if a)
        got_real = sum(1 for a in actions if a != "DoNothing")
        assert got_real == expected_real, (case.parent.name, actions)
