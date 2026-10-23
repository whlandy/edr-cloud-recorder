"""2026-10-23 评审轮新增：trace 校验 / 回放 / 评估的边界与幂等。

覆盖面：零步/部分步轨迹、环外结构损坏、非法断言配置的 fail-closed、
损坏文件加载、重复执行与状态隔离、评估对重复/多余步骤的判定。
全部不依赖真实浏览器（假 page/假 locator），生产代码零改动。
"""

import copy
import json

import pytest
import trace_schema as ts

from replay_trace import (
    TraceReplayError, _assert_locator, evaluate_trace, load_trace,
    replay_trace, validate_trace,
)
from trace_v1 import to_v2


def _two_step_trace(*, optional_first=False, status="ready"):
    """flat 意图 → 生产 v2 形状（顶层节点 + $meta）。测试断言按 v2 形状改写。"""
    first = {"status": "ready", "next": "step-0002",
             "selector": {"kind": "css", "sel": 'locator("#a")'},
             "action": {"type": "Click", "param": {}}}
    if optional_first:
        first["optional"] = True
    second = {"status": "ready", "next": None,
              "selector": {"kind": "css", "sel": 'locator("#b")'},
              "action": {"type": "Click", "param": {}}}
    return to_v2({"name": "save-flow", "startUrl": "https://app.example/form",
                  "status": status, "entry": "step-0001",
                  "steps": {"step-0001": first, "step-0002": second}})


def _ok_step(**extra):
    step = {"status": "ready", "next": None, **extra}
    return step


# ── validate_trace：结构边界 ─────────────────────────────────────────


class TestValidateTraceEdges:
    def test_zero_steps_with_null_entry_is_valid(self):
        trace = to_v2({"entry": None, "steps": {}})
        assert validate_trace(trace) == []

    def test_zero_steps_with_non_null_entry_rejected(self):
        trace = to_v2({"entry": None, "steps": {}})
        trace[ts.META_KEY]["attach"]["entry"] = "step-0001"
        with pytest.raises(TraceReplayError, match="entry 必须是 null|不存在"):
            validate_trace(trace)

    def test_entry_pointing_to_missing_step_rejected(self):
        trace = _two_step_trace()
        trace[ts.META_KEY]["attach"]["entry"] = "step-9999"
        with pytest.raises(TraceReplayError, match="entry 不存在"):
            validate_trace(trace)

    def test_next_to_missing_step_rejected(self):
        trace = _two_step_trace()
        trace["step-0002"]["next"] = "step-9999"
        with pytest.raises(TraceReplayError, match="next 指向不存在的步骤"):
            validate_trace(trace)

    def test_unsupported_action_rejected(self):
        trace = _two_step_trace()
        trace["step-0002"]["action"]["type"] = "Hover"
        with pytest.raises(TraceReplayError, match="不支持的动作"):
            validate_trace(trace)

    def test_unreachable_step_rejected(self):
        trace = _two_step_trace()
        trace["step-0003"] = to_v2({"entry": None, "steps": {}})["placeholder"] \
            if False else _ok_step(
                selector={"kind": "css", "sel": 'locator("#c")'},
                action={"type": "Click", "param": {}})
        with pytest.raises(TraceReplayError, match="不可达"):
            validate_trace(trace)

    def test_validation_does_not_mutate_input(self):
        # 状态隔离红线：校验必须纯 —— 同一 trace 反复校验结果一致、
        # 输入不被改写（回放器重复运行/并发读取都依赖这一点）。
        trace = _two_step_trace()
        snapshot = copy.deepcopy(trace)
        first = validate_trace(trace)
        second = validate_trace(trace)
        assert first == second == ["step-0001", "step-0002"]
        # to_v2 转出的 $meta attach.entry 未被我改过的分支不受影响
        assert trace == snapshot

    def test_v1_trace_gets_actionable_hint(self):
        v1 = {"schema": "edr.success-trace/v1", "name": "x",
              "entry": "step-0001", "steps": {}}
        with pytest.raises(TraceReplayError, match="重新录制|v2"):
            validate_trace(v1)

    @pytest.mark.parametrize("root", [[1, 2, 3], "trace", 42, None, True,
                                      3.14],
                             ids=["list", "str", "int", "null", "bool", "float"])
    def test_json_non_object_root_fails_with_format_error(self, root):
        # F2 修复（2026-10-23 第一批）：全部非对象根统一 TraceFormatError
        # 级拒绝（经 validate_trace 包装为 TraceReplayError），不再裸
        # AttributeError。三个入口同根因同防线。
        with pytest.raises(TraceReplayError):
            validate_trace(root)

    def test_evaluate_trace_non_object_root_rejected(self):
        execution = {"schema": "edr.execution-trace/v1", "status": "success",
                     "steps": [], "goldenSchema": "edr.success-trace/v2"}
        with pytest.raises(TraceReplayError):
            evaluate_trace([1, 2, 3], execution)


# ── load_trace：损坏输入 ─────────────────────────────────────────────


class TestLoadTraceCorruption:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(OSError):
            load_trace(tmp_path / "nope.json")

    def test_corrupt_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text('{"schema": "edr.success-trace/v2", ', encoding="utf-8")
        with pytest.raises(Exception):
            load_trace(p)

    def test_json_root_must_be_object(self, tmp_path):
        p = tmp_path / "list.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(Exception):
            load_trace(p)

    def test_utf8_bom_rejected_with_error(self, tmp_path):
        # 现状：read_text(encoding="utf-8") 不剥 BOM，json.loads 明确报错
        # （带可读信息），不会静默产出坏 trace。钉住“报错”而非“能读”。
        p = tmp_path / "bom.json"
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps(
            {"schema": "edr.success-trace/v2"}).encode("utf-8"))
        with pytest.raises(Exception, match="BOM|Expecting"):
            load_trace(p)

    def test_empty_file_rejected(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        with pytest.raises(Exception):
            load_trace(p)


# ── _assert_locator：断言配置 fail-closed（先于任何 locator 使用） ────


class _ExplosiveLocator:
    """配置校验必须先于 locator 使用 —— 该 locator 一被碰就炸。"""

    def __getattr__(self, name):
        raise AssertionError(f"配置非法却触达了 locator.{name}")

    def count(self):
        raise AssertionError("配置非法却触达了 locator.count()")


class TestAssertLocatorConfigFailsClosed:
    def test_visible_with_non_equals_op_rejected(self):
        with pytest.raises(TraceReplayError, match="不支持算子"):
            _assert_locator(_ExplosiveLocator(),
                            {"assertion": "visible", "expected": True,
                             "op": "at_least"}, 100)

    def test_count_with_bool_expected_rejected(self):
        with pytest.raises(TraceReplayError, match="整数"):
            _assert_locator(_ExplosiveLocator(),
                            {"assertion": "count", "expected": True,
                             "op": "equals"}, 100)

    def test_count_with_non_count_op_rejected(self):
        with pytest.raises(TraceReplayError, match="不支持的比较算子|at_least"):
            _assert_locator(_ExplosiveLocator(),
                            {"assertion": "count", "expected": 1,
                             "op": "greater_than"}, 100)

    def test_unknown_assertion_rejected(self):
        with pytest.raises(TraceReplayError, match="不支持的断言"):
            _assert_locator(_ExplosiveLocator(),
                            {"assertion": "innerHTML", "expected": "x"}, 100)

    def test_text_with_unknown_op_rejected(self):
        with pytest.raises(TraceReplayError, match="不支持的比较算子"):
            _assert_locator(_ExplosiveLocator(),
                            {"assertion": "text", "expected": "x",
                             "op": "regex_equals"}, 100)

    @pytest.mark.parametrize("bad", [0, -5, True, 1.5, "60", 86401])
    def test_timerange_bad_duration_rejected(self, bad):
        with pytest.raises(TraceReplayError, match="时长非法"):
            _assert_locator(
                _ExplosiveLocator(),
                {"assertion": "text", "expected": "10:20:00",
                 "expectedFrom": {"kind": "timerange",
                                  "durationSeconds": bad}}, 100)

    def test_timerange_missing_duration_ok(self):
        # 缺省时长合法（HH:MM → 整分钟）；期望值不匹配 → 正常轮询超时，
        # 不是配置错误。用 0.1s 超时控制时长。
        with pytest.raises(AssertionError):
            _assert_locator(_ExplosiveLocator(),  # noqa: 期望值路径碰不到爆炸体
                            {"assertion": "text", "expected": "10:20:00",
                             "expectedFrom": {"kind": "timerange"}}, 100)


# ── replay_trace：零步 / 幂等 / 状态隔离（假 page） ──────────────────


class _FakePage:
    def __init__(self):
        self.actions = []

    def goto(self, url, **kwargs):
        self.actions.append(("goto", url))

    def locator(self, selector, **kwargs):
        raise AssertionError("零步轨迹不应触达任何 locator")

    def get_by_text(self, text, **kwargs):
        raise AssertionError("零步轨迹不应触达任何 locator")


class _ClickOkPage(_FakePage):
    def __init__(self):
        super().__init__()
        self.clicked = []

    def locator(self, selector, **kwargs):
        page = self

        class _Loc:
            def count(self):
                return 1

            def wait_for(self, **kwargs):
                pass

            def click(self, **kwargs):
                page.clicked.append(selector)

        return _Loc()


class TestReplayEdges:
    def test_zero_step_trace_replays_success_without_actions(self):
        trace = to_v2({"entry": None, "steps": {}})
        execution = replay_trace(_FakePage(), trace, navigate=False)
        assert execution["status"] == "success"
        assert execution["steps"] == []

    def test_same_trace_object_replays_identically_twice(self):
        # 状态隔离：同一 dict 连续回放两次，结果逐字段一致
        trace = _two_step_trace()
        e1 = replay_trace(_ClickOkPage(), trace, navigate=False)
        e2 = replay_trace(_ClickOkPage(), trace, navigate=False)
        assert e1["status"] == e2["status"] == "success"
        assert [s["nodeId"] for s in e1["steps"]] == \
            [s["nodeId"] for s in e2["steps"]]

    def test_not_ready_trace_rejected_before_any_action(self):
        trace = _two_step_trace(status="incomplete")
        with pytest.raises(TraceReplayError, match="ready"):
            replay_trace(_ClickOkPage(), trace, navigate=False)

    def test_invalid_targeting_rejected_immediately(self):
        with pytest.raises(ValueError, match="targeting"):
            replay_trace(_FakePage(), _two_step_trace(), navigate=False,
                         targeting="css_only")


# ── evaluate_trace：重复步骤 / 多余动作 / 部分成功 ────────────────────


def _success_execution(trace, *, duplicate_last=False, extra=False,
                       fail_first=False, retries=0):
    order = validate_trace(trace)
    steps = []
    for node_id in order:
        step = {"nodeId": node_id, "expectedAction": "Click",
                "actualAction": "Click", "status": "running", "responses": [],
                "retries": retries}
        if fail_first and node_id == order[0]:
            step.update(status="failed",
                        error="assertion failed", failureCategory="assertion")
        else:
            step["status"] = "success"
        steps.append(step)
    if duplicate_last:
        steps.append(dict(steps[-1]))
    if extra:
        steps.append({"nodeId": "step-9999", "expectedAction": "Click",
                      "actualAction": "Click", "status": "success",
                      "responses": [], "retries": 0})
    return {"schema": "edr.execution-trace/v1", "status": "success"
            if not fail_first else "failed", "steps": steps,
            "goldenSchema": "edr.success-trace/v2"}


class TestEvaluateTraceEdges:
    def test_duplicate_step_execution_fails_task_success(self):
        # 幂等红线：同一步执行两次不得伪装成干净通过 ——
        # observed_path 含重复 → taskSuccess=False，且计入多余动作。
        trace = _two_step_trace()
        evaluation = evaluate_trace(
            trace, _success_execution(trace, duplicate_last=True))
        assert evaluation["taskSuccess"] is False
        assert evaluation["extraActionCount"] >= 1

    def test_extra_unknown_node_is_counted_not_accepted(self):
        # 设计语义（与 test_evaluation_penalizes_extra_actions_and_retries 一致）：
        # 金标路径之外的未知节点计入 extraActionCount、拉低 trajectoryEfficiency，
        # 但不翻转 taskSuccess（任务完成与否看金标路径本身）；仅空金标 +
        # 非 空执行 才直接判失败（见 empty_golden 拒收用例）。
        trace = _two_step_trace()
        clean = evaluate_trace(trace, _success_execution(trace))
        evaluation = evaluate_trace(trace, _success_execution(trace, extra=True))
        assert evaluation["extraActionCount"] == 1
        assert evaluation["trajectoryEfficiency"] < clean["trajectoryEfficiency"]
        assert evaluation["score"] < clean["score"]

    def test_partial_failure_halves_completion_and_fails_task(self):
        trace = _two_step_trace()
        evaluation = evaluate_trace(trace, _success_execution(trace,
                                                              fail_first=True))
        assert evaluation["stepCompletionRate"] == 0.5
        assert evaluation["taskSuccess"] is False
        assert evaluation["failureCategories"] == {"assertion": 1}

    def test_missing_steps_key_rejected(self):
        trace = _two_step_trace()
        execution = _success_execution(trace)
        execution.pop("steps")
        with pytest.raises(TraceReplayError, match="数组"):
            evaluate_trace(trace, execution)

    def test_step_without_node_id_rejected(self):
        trace = _two_step_trace()
        execution = _success_execution(trace)
        execution["steps"][0].pop("nodeId")
        with pytest.raises(TraceReplayError, match="nodeId"):
            evaluate_trace(trace, execution)

    def test_negative_retries_rejected(self):
        trace = _two_step_trace()
        execution = _success_execution(trace, retries=-1)
        with pytest.raises(TraceReplayError, match="非负整数"):
            evaluate_trace(trace, execution)

    def test_bool_retries_rejected(self):
        trace = _two_step_trace()
        execution = _success_execution(trace)
        execution["steps"][0]["retries"] = True
        with pytest.raises(TraceReplayError, match="非负整数"):
            evaluate_trace(trace, execution)

    def test_zero_step_evaluation_is_clean_success(self):
        trace = to_v2({"entry": None, "steps": {}})
        evaluation = evaluate_trace(
            trace, {"schema": "edr.execution-trace/v1", "status": "success",
                    "steps": [], "goldenSchema": "edr.success-trace/v2"})
        assert evaluation["taskSuccess"] is True
        assert evaluation["score"] == 100.0
