"""用**真正的 maa-fw** 验一遍我们产出的轨迹。

其余关于 v2 形状的检查都是**镜像测试** —— 照着 maa-fw 的规则在我们这边写，
从没让 maa-fw 真的加载过产物。这类检查抓不住的正是最坏的一种失败：
加载成功、能跑、什么都不做。

实测拿 edr-wd 的桌面轨迹喂给 maa-fw 就是这个下场：5 个节点 coerce 成功 4 个，
但每个都变成 DirectHit + DoNothing，`next: "step-0002"` 被按字符拆成
['s','t','e','p',…]。两边都不报错，两边都以为没事。

maa-fw 不在本机就跳过 —— 这条检查是**加强**，不该让别人装不了 maa-fw 就跑不了测试。
"""

import pytest

from generate_trace import generate_trace
from maa_contract import MaaFwUnavailable, contract_problems


@pytest.fixture(scope="module")
def maa_fw():
    try:
        from maa_contract import maa_fw_home
        return maa_fw_home()
    except MaaFwUnavailable as reason:
        pytest.skip(f"本机没有 maa-fw：{reason}")


def _trace(rec):
    return generate_trace(rec["steps"], rec["net"],
                          start_url=rec["startUrl"], name="contract")


def test_generated_trace_survives_maa_fw(recording, maa_fw):
    """maa-fw 逐个 coerce 我们产出的节点，不能有任何问题。"""
    assert contract_problems(_trace(recording)) == []


def test_actions_outside_maa_fw_must_be_marked(recording, maa_fw):
    """SetSwitch / Check / Uncheck 这些「读了状态再决定拨不拨」的动作，
    maa-fw 编译不出来。标了 web-only 才算已知；不标就是没人知道桌面侧会怎么办 ——
    而它多半会退化成一次盲点击，方向取决于当时的状态，拨反了也不报错。"""
    import trace_schema as ts

    trace = _trace(recording)
    unmarked = [
        name for name, node in ts.nodes(trace)
        if (node["action"]["type"] not in ts.MAA_ACTIONS
            and ts.provenance(node).get("actionScope") != ts.VERIFY_SCOPE_WEB)
    ]
    assert unmarked == [], unmarked


def test_the_checker_catches_a_silently_dropped_action(recording, maa_fw):
    """守卫本身得守得住：把一个动作改成 DoNothing，必须被指出来。

    这正是 edr-wd 的桌面轨迹喂给 maa-fw 时发生的事 —— 而它当时没有任何人喊。
    """
    import trace_schema as ts

    trace = _trace(recording)
    victim = next(name for name, node in ts.nodes(trace)
                  if (ts.attach(node).get("gui_target") or {}).get("source_action"))
    trace[victim]["action"] = {"type": "DoNothing", "param": {}}

    problems = contract_problems(trace)
    assert any(victim in p and "DoNothing" in p for p in problems), problems


def test_the_checker_catches_a_character_split_next(recording, maa_fw):
    """next 写成字符串时 maa-fw 会按字符迭代 —— 节点链变成一串单字母。"""
    import trace_schema as ts

    trace = _trace(recording)
    victim = next(name for name, node in ts.nodes(trace) if node.get("next"))
    trace[victim]["next"] = "step_0002"

    problems = contract_problems(trace)
    assert any(victim in p and "字符" in p for p in problems), problems


@pytest.mark.skipif(
    not (__import__("pathlib").Path.home() / "ai-projects/edr-wd/recordings").is_dir(),
    reason="本机没有 edr-wd 录制")
def test_exported_desktop_traces_survive_maa_fw(maa_fw):
    """桌面轨迹导出成 v2 之后，maa-fw 必须真的能加载并保住动作。

    这是这条链的**终点检查**：edr-wd 产物 → 导出 → maa-fw。
    没有这一条的话，导出器可以产出一堆 DoNothing 而没人发现 ——
    那正是没有导出器时的原始状态。
    """
    import json
    from pathlib import Path

    from desktop_to_v2 import convert

    cases = sorted((Path.home() / "ai-projects/edr-wd/recordings").glob(
        "*/golden-trace.json"))
    assert cases, "没有可用的 edr-wd 录制"
    for case in cases:
        trace = convert(json.loads(case.read_text(encoding="utf-8")))
        assert contract_problems(trace) == [], case.parent.name


# ── 链条真的走得通 ──
# coerce 只证明「节点读得懂」。读得懂不等于走得通：$meta 不惰性会被当成一步、
# next 指错会让链条断在中间、动作在 coerce 时被改写会让执行器收到 DoNothing ——
# 这三件事逐个 coerce 全都看不出来。

def _walk(trace):
    from maa_contract import traversal_problems
    return traversal_problems(trace)


def test_generated_trace_walks_end_to_end(recording, maa_fw):
    assert _walk(_trace(recording)) == []


def test_walk_catches_a_broken_next(recording, maa_fw):
    import trace_schema as ts

    trace = _trace(recording)
    victim = next(name for name, node in ts.nodes(trace) if node.get("next"))
    trace[victim]["next"] = ["step_9999"]
    assert any("顺序" in p for p in _walk(trace)), _walk(trace)


def test_walk_catches_an_action_rewritten_during_coercion(recording, maa_fw):
    """文件里写的和 maa-fw 实际收到的必须逐个对上。

    不能拿变异后的轨迹跟它自己比 —— 动作被改成 DoNothing 时「本该不做事的
    节点」也跟着 +1，那种检查永远发现不了问题。
    """
    import trace_schema as ts

    trace = _trace(recording)
    victim = next(name for name, _ in ts.nodes(trace))
    trace[victim]["action"] = {"typo": "Click"}          # coerce 会退成 DoNothing
    assert any("不一致" in p for p in _walk(trace)), _walk(trace)


def test_walk_catches_a_non_inert_meta(recording, maa_fw):
    """$meta 真正的风险是「界面完全对不上」时被一路落到 —— 它无条件命中。

    顺着走一遍查不出这件事：第一步就成功返回了，$meta 根本没轮到。
    """
    import trace_schema as ts

    trace = _trace(recording)
    trace[ts.META_KEY]["max_hit"] = None
    trace[ts.META_KEY]["attach"]["stats"]["hit_count"] = 0
    assert any("不惰性" in p for p in _walk(trace)), _walk(trace)


def test_walk_catches_a_cycle(recording, maa_fw):
    import trace_schema as ts

    trace = _trace(recording)
    names = [name for name, _ in ts.nodes(trace)]
    trace[names[2]]["next"] = [names[1]]
    assert any("回头路" in p for p in _walk(trace)), _walk(trace)
