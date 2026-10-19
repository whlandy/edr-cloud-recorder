"""P2（G2）验收：execution 步骤 failureCategory 结构化分类。

设计文档：references/assertion-hardening-design.md 阶段 2。
守的红线：质量报告的「失败类型分布」必须可算 —— 失败类别落在
结构化字段上；映射按异常类型/来源确定性判定，未知一律 unknown
（不猜、不错标）；分类是元数据，**不进评分公式**。
修前这些测试必挂：execution 步骤没有 failureCategory 字段，
evaluate_trace 结果没有 failureCategories 统计。
"""

import trace_schema as ts
from replay_trace import (
    EXECUTION_SCHEMA,
    evaluate_trace as _evaluate_trace,
    replay_trace as _replay_trace,
)
from trace_v1 import to_v2


def _trace(node):
    return {
        "schema": "edr.success-trace/v2",
        "name": "save-flow",
        "startUrl": "https://app.example/form",
        "status": "ready",
        "entry": "step-0001",
        "steps": {"step-0001": {"status": "ready", "next": None, **node}},
    }


def evaluate_trace(golden, execution):
    return _evaluate_trace(to_v2(golden), execution)


def replay_trace(page, trace, **kwargs):
    return _replay_trace(page, to_v2(trace), **kwargs)


def _assert_node():
    return {"selector": {"sel": 'locator("#status")'},
            "action": {"type": "Assert",
                        "param": {"assertion": "text", "expected": "Saved"}}}


def test_evaluate_trace_counts_failure_categories():
    """透传 + 计数；无字段的旧 execution 计 unknown；评分对分类零敏感。"""
    golden = _trace(_assert_node())
    # 与 evaluate_trace 内部同一口径取 schema（不自己猜 v2 的 meta 结构）
    schema = ts.meta(to_v2(golden))["schema"]

    def execution(with_category):
        step = {"nodeId": "step-0001", "status": "failed",
                "error": "断言失败: text actual='Wrong' expected='Saved'"}
        if with_category:
            step["failureCategory"] = "assertion"
        return {
            "schema": EXECUTION_SCHEMA,
            "goldenSchema": schema,
            "status": "failed",
            "steps": [step],
        }

    new = evaluate_trace(golden, execution(True))
    old = evaluate_trace(golden, execution(False))
    assert new["failureCategories"] == {"assertion": 1}
    assert old["failureCategories"] == {"unknown": 1}
    # 分类是元数据：带不带字段，分数必须一模一样（评分公式零变化）
    assert new["score"] == old["score"]


def test_failed_assertion_step_is_categorized_assertion():
    """真实回放：断言失败 → 该步骤 failureCategory == assertion。"""
    class Locator:
        def inner_text(self):
            return "Wrong text"

    class Page:
        def goto(self, url):
            pass

        def locator(self, selector):
            return Locator()

    execution = replay_trace(
        Page(), _trace(_assert_node()), targeting="visual_only", timeout_ms=100)

    assert execution["status"] == "failed"
    step = execution["steps"][0]
    assert step["status"] == "failed"
    assert step["failureCategory"] == "assertion"


def test_step_level_io_failure_is_categorized_infrastructure():
    """真实回放：步骤级 IO 失败（模板/证据文件缺失同类）→ infrastructure。"""
    class Page:
        def goto(self, url):
            pass

        def locator(self, selector):
            raise FileNotFoundError(2, "no such file", "/templates/missing.png")

    trace = _trace({"selector": {"sel": 'locator("#save")'},
                    "action": {"type": "Click", "param": {}}})
    execution = replay_trace(Page(), trace, timeout_ms=100)

    assert execution["status"] == "failed"
    assert execution["steps"][0]["failureCategory"] == "infrastructure"
