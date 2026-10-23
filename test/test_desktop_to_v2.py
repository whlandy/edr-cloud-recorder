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


def test_time_range_verifiers_are_marked_desktop_only():
    """时间窗断言（text_time_range / window_text_time_range）同样是
    运行时才算得出的期望 —— edr-wd schema 新加的两个类型。
    转换器若漏标，maa-fw 会以为验过了（静默假验证）。
    修前本测试必挂：scope 不标。"""
    trace = convert(_desktop({
        "actionId": None,
        "verifiers": [
            {"type": "text_time_range", "expected": "10:20",
             "timeoutSeconds": 10.0},
            {"type": "window_text_time_range", "expected": "2026-10-09 10:20",
             "timeoutSeconds": 10.0},
        ]}))
    verification = ts.verification(trace["step_0001"])
    assert verification["scope"] == ts.VERIFY_SCOPE_DESKTOP
    assert [v["type"] for v in verification["verifiers"]] == [
        "text_time_range", "window_text_time_range"]


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
def _desktop_golden_is_replayable_from_dict(golden: dict) -> bool:
    """录制是否处于可回放语料状态：ready 且有非空 steps/entry。

    只过滤「还没录完/没导完」的条目（如 endcloud_demo_20261022：
    status=incomplete、steps={}）；**ready 但损坏/无动作的录制不在
    过滤之列**，进入判定后仍须失败 —— 不能用过滤掩盖真实断链。
    """
    return (golden.get("status") == "ready"
            and bool(golden.get("steps"))
            and golden.get("entry") is not None)


def _desktop_golden_is_replayable(case) -> bool:
    return _desktop_golden_is_replayable_from_dict(
        json.loads(case.read_text(encoding="utf-8")))


def _assert_replayable_actions(golden: dict, name: str) -> int:
    """单条 ready 语料的转换判定（真实测试与反例共用同一函数）：

    1. ready 但**无动作**（源步骤全无 actionId）→ fail —— 静默全
       DoNothing 正是要堵的丢失形态；
    2. 转换后真实动作数必须与源一致（不得静默变 DoNothing）。
    """
    trace = convert(golden)
    actions = [node["action"]["type"] for _, node in ts.nodes(trace)]
    source_actions = [s.get("actionId") for s in golden["steps"].values()]
    expected_real = sum(1 for a in source_actions if a)
    got_real = sum(1 for a in actions if a != "DoNothing")
    if expected_real == 0:
        pytest.fail(f"ready 录制没有任何可回放动作（全 DoNothing）: {name}")
    assert got_real == expected_real, (name, actions)
    return got_real


def test_real_edr_wd_recordings_convert_and_keep_their_actions():
    """真实语料：转换后不能出现「整条轨迹全是 DoNothing」这种静默丢失。

    未就绪条目（非 ready / 零步 / 无 entry）逐条点名；**先跑完全部
    有效语料再跳过**，避免首个未就绪条目中止整个用例。
    """
    cases = sorted((EDR_WD / "recordings").glob("*/golden-trace.json"))
    skipped = [c.parent.name for c in cases
               if not _desktop_golden_is_replayable(c)]
    replayable = [c for c in cases if _desktop_golden_is_replayable(c)]
    for case in replayable:
        golden = json.loads(case.read_text(encoding="utf-8"))
        _assert_replayable_actions(golden, case.parent.name)
    if skipped:
        pytest.skip("跳过未就绪录制（非 ready 或零步）: " + ", ".join(skipped))


def test_ready_but_corrupt_recording_fails_not_skipped():
    """F3 过滤边界：过滤只挡「非 ready / 零步 / 无 entry」；ready 但步骤
    损坏（无 actionId 等形态）的录制**必须照常进入判定并失败**，
    不得被过滤掩盖或静默全 DoNothing 放过。"""
    ready_broken = {"schema": DESKTOP_SCHEMA, "status": "ready", "name": "x",
                    "sourceRecording": {}, "catalog": {}, "environment": {},
                    "entry": "s1",
                    "steps": {"s1": {"status": "ready", "next": None,
                                     "bogus": True}},
                    "cleanup": []}
    assert _desktop_golden_is_replayable_from_dict(ready_broken) is True
    with pytest.raises(pytest.fail.Exception,
                       match="ready 录制没有任何可回放动作"):
        _assert_replayable_actions(ready_broken, "synthetic-ready-broken")
