"""record.py 完成摘要「回放就绪」判定的回归测试。

缺陷复现：record.py 原实现读 `trace["$meta"].get("status")`，但真实 v2 的 status
在 `$meta.attach.status`，前者恒为 None → 一条**合法 ready** 的轨迹会在完成摘要里
被误报成「尚未全部可回放」。正确来源是 `trace_schema.meta(trace)["status"]`
（与 replay_trace 一致），status 齐在 attach 之下（ts.meta 已归一）。

`_replayability` 是 record.py 完成摘要用的纯判定：validate_trace 过 + status==ready
才可回放。这里直接喂三种输入：
- ready：合法 v2、过契约、status=ready → 判定可回放；
- incomplete：过契约、status=incomplete → 判定不可回放且点名缺模板；
- 无效契约：过不了 validate_trace → 判定不可回放且给原因。
"""

from record import _replayability
from trace_v1 import to_v2
from generate_trace import generate_trace


def _flat(status="ready"):
    """最省事的平铺意图，经生产 trace_schema 组装成合法 v2 轨迹。"""
    return {
        "schema": "edr.success-trace/v2",
        "name": "save-flow",
        "startUrl": "https://app.example/form",
        "status": status,
        "entry": "step-0001",
        "steps": {"step-0001": {"status": "ready", "next": None}},
    }


# ── G3 项 1：云侧「generate 自检 ready」纳入 pytest ─────────────────
# record 完成摘要走的 `_replayability`，应和 generate_trace 产出对齐：
# 非位置步骤（断言）录出来的轨迹 status=ready、过 validate_trace → 判定可回放；
# 位置步骤缺模板 → status=incomplete → 判定不可回放并给可操作原因。


def test_generate_trace_assert_recording_is_self_checked_ready():
    trace = generate_trace(
        [{"id": "a", "t": 1, "type": "assert",
          "sel": 'getByText("已应用")', "assertion": "text", "expected": "已应用"}],
        [], name="x", start_url="https://a/",
    )
    ok, reason = _replayability(trace)
    assert ok is True
    assert reason == ""


def test_generate_trace_positional_without_template_is_incomplete():
    trace = generate_trace(
        [{"id": "c", "t": 1, "type": "click", "kind": "css",
          "sel": 'locator("#x")', "css": "#x"}],
        [], name="x", start_url="https://a/",
    )
    ok, reason = _replayability(trace)
    assert ok is False
    assert "缺模板" in reason


def test_replayability_recognizes_ready_v2():
    # 合法 v2 ready：$meta.attach.status == ready。旧实现读 $meta.status（None）
    # 会误报不可回放 —— 本条专门守它。
    ok, reason = _replayability(to_v2(_flat("ready")))
    assert ok is True
    assert reason == ""


def test_replayability_recognizes_incomplete():
    ok, reason = _replayability(to_v2(_flat("incomplete")))
    assert ok is False
    assert "缺模板" in reason


def test_replayability_reports_invalid_contract():
    bad = to_v2(_flat("ready"))
    bad["step-0001"]["action"] = {"type": "Hover", "param": {}}
    ok, reason = _replayability(bad)
    assert ok is False
    assert "不支持的动作" in reason
