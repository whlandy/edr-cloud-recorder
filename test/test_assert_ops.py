"""比较算子 / 数量断言 / 时间区间断言的测试。

三层：
1. 纯函数（matches_assert / parse_time_range / matches_time_range）—— 边界语义
   （半开区间、午夜回绕、数值比较 vs 字典序）逐条钉死；
2. 假页面回放 —— 回放器 _assert_locator 的算子/数量/timerange 接线；
3. 真实浏览器端到端 —— 同一套断言在 chromium 里跑通完整 replay_trace。
"""

import json

import pytest

from rec_assert import (
    expect_attribute, expect_count, expect_value, matches_assert,
    matches_time_range, parse_time_range,
)
from replay_trace import replay_trace as _replay_trace
from trace_v1 import to_v2


def replay_trace(page, trace, **kwargs):
    """与 test_trace_replay 同一包装：平铺节点表意，进回放器之前统一翻成 v2 ——
    真实形状由 trace_v1 与生产生成器共用，测试不手抄嵌套布局。"""
    return _replay_trace(page, to_v2(trace), **kwargs)

# ────────────────────────── 1. 纯函数 ──────────────────────────


class TestMatchesAssert:
    def test_equals_normalizes_whitespace_like_playwright(self):
        assert matches_assert("  Saved\n   successfully  ", "Saved successfully")
        # 归一化后相等 —— 与 Playwright to_have_text 同一语义
        assert matches_assert("Saved  x", "Saved x")
        assert not matches_assert("Saved y", "Saved x")

    def test_contains_normalizes_too(self):
        assert matches_assert("a  b c", "b c", "contains")
        assert matches_assert("nothing", "err", "contains") is False
        assert matches_assert("error: bad", "warning", "not_contains")

    def test_starts_ends_with(self):
        assert matches_assert("abc.", "abc", "starts_with")
        assert matches_assert("abc.", "c.", "ends_with")
        assert not matches_assert("xabc", "abc", "starts_with")

    def test_matches_is_fullmatch_not_search(self):
        assert matches_assert("err-42", r"err.*", "matches")
        # 搜索语义会让它通过 —— 全串匹配不允许
        assert not matches_assert("a err b", r"err.*", "matches")
        assert not matches_assert("bad", r"err.*", "matches")
        # 非法正则按「不通过」处理，不按错误处理
        assert not matches_assert("x", r"(", "matches")

    def test_blank_ops(self):
        assert matches_assert("   ", None, "is_blank")
        assert matches_assert(None, None, "is_blank")
        assert matches_assert("x", None, "is_not_blank")
        assert not matches_assert("   ", None, "is_not_blank")

    def test_numeric_ops_compare_as_numbers_not_strings(self):
        # 字典序里 '10' < '9'，数值比较里不是 —— 这正是当初禁止字符串比较的原因
        assert matches_assert("10", "9", "greater_than")
        assert matches_assert("3", "5", "less_than")
        assert not matches_assert("5", "5", "greater_than")

    def test_numeric_ops_fail_on_non_numeric(self):
        assert not matches_assert("abc", "5", "greater_than")
        assert not matches_assert("7", "x", "less_than")

    def test_unknown_op_raises(self):
        with pytest.raises(ValueError):
            matches_assert("x", "y", "regex-ish")


class TestTimeRange:
    def test_hhmm_covers_whole_minute(self):
        start, dur = parse_time_range("10:10")
        assert (start, dur) == (10 * 3600 + 10 * 60, 60)
        assert matches_time_range("2026-10-21 10:10:59", "10:10") == ["10:10:59"]
        assert matches_time_range("2026-10-21 10:11:00", "10:10") == []

    def test_start_inclusive_end_exclusive(self):
        assert matches_time_range("00:28:56", "00:28:56", 1) == ["00:28:56"]
        assert matches_time_range("00:28:57", "00:28:56", 1) == []
        assert matches_time_range("00:28:56", "00:28:55", 1) == []

    def test_duration_extends_end(self):
        assert matches_time_range("10:10:30", "10:10:00", 40)
        assert not matches_time_range("10:10:40", "10:10:00", 40)

    def test_midnight_wrap(self):
        assert matches_time_range("23:59:50 之后 00:00:30", "23:59:50", 120) == [
            "23:59:50", "00:00:30",
        ]
        assert not matches_time_range("00:02:00", "23:59:50", 120)

    def test_token_can_be_anywhere_in_text(self):
        assert matches_time_range("检查时间 10:20:59", "10:20") == ["10:20:59"]
        assert matches_time_range(None, "10:20") == []

    def test_does_not_eat_date_digits(self):
        # 日期里的 10-21 不能被当成时间记号；时间记号前的数字/冒号/符号要挡住
        assert matches_time_range("2026-10-21 09:05:00", "09:05") == ["09:05:00"]

    @pytest.mark.parametrize("bad", ["25:00", "9:5", "10:60", "10:10:60", ""])
    def test_bad_prefix_raises(self, bad):
        with pytest.raises(ValueError):
            parse_time_range(bad)

    @pytest.mark.parametrize("bad", [0, -1, 86401, "60", True])
    def test_bad_duration_raises(self, bad):
        with pytest.raises(ValueError):
            parse_time_range("10:10:30", bad)


class _FakeLocator:
    def __init__(self, *, value=None):
        self._value = value

    def input_value(self):
        if self._value is None:
            raise RuntimeError("no element")
        return self._value

    def count(self):
        return 3


def test_expect_value_with_ops():
    assert expect_value(_FakeLocator(value="7"), "5", op="greater_than", timeout=0.3) == "7"
    with pytest.raises(AssertionError):
        expect_value(_FakeLocator(value="abc"), "5", op="greater_than", timeout=0.3)


def test_expect_count_ops():
    assert expect_count(_FakeLocator(), 2, op="at_least", timeout=0.3) == 3
    assert expect_count(_FakeLocator(), 3, timeout=0.3) == 3
    with pytest.raises(AssertionError):
        expect_count(_FakeLocator(), 5, timeout=0.3)
    with pytest.raises(ValueError):
        expect_count(_FakeLocator(), "3", timeout=0.3)


# ────────────────────────── 2. 假页面回放 ──────────────────────────


def _trace(action_param, sel='locator("#x")'):
    return {
        "schema": "edr.success-trace/v2",
        "name": "assert-ops",
        "startUrl": "https://app.example/x",
        "status": "ready",
        "entry": "step-0001",
        "steps": {"step-0001": {
            "status": "ready", "next": None,
            "selector": {"sel": sel},
            "action": {"type": "Assert", "param": action_param},
        }},
    }


class _Page:
    def __init__(self, locator):
        self._locator = locator

    def goto(self, url):
        pass

    def locator(self, selector):
        return self._locator


def test_replay_text_ops():
    page = _Page(_TextLocator("订单 abc. done"))
    execution = replay_trace(page, _trace(
        {"assertion": "text", "expected": "abc", "op": "contains"}),
        navigate=False, timeout_ms=200)
    assert execution["status"] == "success"

    execution = replay_trace(_Page(_TextLocator("all good")), _trace(
        {"assertion": "text", "expected": "err", "op": "not_contains"}),
        navigate=False, timeout_ms=200)
    assert execution["status"] == "success"

    execution = replay_trace(_Page(_TextLocator("all good")), _trace(
        {"assertion": "text", "expected": "err", "op": "contains"}),
        navigate=False, timeout_ms=200)
    assert execution["status"] == "failed"


def test_replay_value_numeric_op():
    class V:
        def input_value(self):
            return "10"
        def count(self):
            return 0

    execution = replay_trace(_Page(V()), _trace(
        {"assertion": "value", "expected": "9", "op": "greater_than"}),
        navigate=False, timeout_ms=200)
    assert execution["status"] == "success"


def test_replay_count_ops():
    class C:
        def count(self):
            return 3

    execution = replay_trace(_Page(C()), _trace(
        {"assertion": "count", "expected": 2, "op": "at_least"}),
        navigate=False, timeout_ms=200)
    assert execution["status"] == "success"

    execution = replay_trace(_Page(C()), _trace(
        {"assertion": "count", "expected": 5}),
        navigate=False, timeout_ms=200)
    assert execution["status"] == "failed"

    # expected 必须是整数 —— 静默接受字符串会把「数量断言」变成「永远失败」；
    # 形状错误要报出要求，而不是含糊的断言失败
    execution = replay_trace(_Page(C()), _trace(
        {"assertion": "count", "expected": "3"}),
        navigate=False, timeout_ms=200)
    assert execution["status"] == "failed"
    assert "整数" in json.dumps(execution, ensure_ascii=False)

    # 可见性/勾选断言没有算子
    class Hidden:
        def is_visible(self):
            return False
        def count(self):
            return 0

    execution = replay_trace(_Page(Hidden()), _trace(
        {"assertion": "visible", "expected": False, "op": "contains"}),
        navigate=False, timeout_ms=200)
    assert execution["status"] == "failed"
    assert "不支持算子" in json.dumps(execution, ensure_ascii=False)


def test_replay_timerange():
    class T:
        def inner_text(self):
            return "检查时间 23:59:55 完成"

    execution = replay_trace(_Page(T()), _trace({
        "assertion": "text", "expected": "23:59:50",
        "expectedFrom": {"kind": "timerange", "durationSeconds": 60},
    }), navigate=False, timeout_ms=200)
    assert execution["status"] == "success"

    execution = replay_trace(_Page(T()), _trace({
        "assertion": "text", "expected": "23:59:55",
        "expectedFrom": {"kind": "timerange", "durationSeconds": 1},
    }), navigate=False, timeout_ms=200)
    # 起点 23:59:55 在内 —— 但同一段文本里 23:59:55 命中，这是「起点算内」的回归
    assert execution["status"] == "success"

    execution = replay_trace(_Page(T()), _trace({
        "assertion": "text", "expected": "23:59:56",
        "expectedFrom": {"kind": "timerange", "durationSeconds": 1},
    }), navigate=False, timeout_ms=200)
    assert execution["status"] == "failed"


def test_replay_visible_false_is_absence():
    class Hidden:
        def is_visible(self):
            return False

    execution = replay_trace(_Page(Hidden()), _trace(
        {"assertion": "visible", "expected": False}),
        navigate=False, timeout_ms=200)
    assert execution["status"] == "success"


class _TextLocator:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text

    def count(self):
        return 0

# ────────────────────────── 3. 真实浏览器端到端 ──────────────────────────

E2E_HTML = """
<!doctype html><meta charset="utf-8"><body>
  <div id="msg">订单保存成功，单号 abc-42</div>
  <div id="time">操作时间 2026-10-21 12:00:30</div>
  <table id="rows">
    <tr><td>1</td></tr><tr><td>2</td></tr><tr><td>3</td></tr>
  </table>
  <div id="gone" style="display:none">不存在了</div>
</body>"""


def _e2e_trace(nodes):
    """nodes: [(selector, param), ...] 按顺序串成一条轨迹。"""
    steps = {}
    for i, (sel, param) in enumerate(nodes, 1):
        sid = f"step-{i:04d}"
        steps[sid] = {
            "status": "ready",
            "next": f"step-{i + 1:04d}" if i < len(nodes) else None,
            "selector": {"sel": f"locator(\"{sel}\")"},
            "action": {"type": "Assert", "param": param},
        }
    return {
        "schema": "edr.success-trace/v2",
        "name": "assert-ops-e2e",
        "startUrl": "https://app.example/e2e",
        "status": "ready",
        "entry": "step-0001",
        "steps": steps,
    }


@pytest.fixture(scope="module")
def browser_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(E2E_HTML)
        yield page
        browser.close()


def test_e2e_new_assertions_pass_in_real_browser(browser_page):
    execution = replay_trace(browser_page, _e2e_trace([
        ("#msg", {"assertion": "text", "expected": "abc-42", "op": "contains"}),
        ("#msg", {"assertion": "text", "expected": "订单保存成功", "op": "starts_with"}),
        ("#rows tr", {"assertion": "count", "expected": 2, "op": "at_least"}),
        ("#time", {"assertion": "text", "expected": "12:00:30",
                   "expectedFrom": {"kind": "timerange", "durationSeconds": 1}}),
        ("#gone", {"assertion": "visible", "expected": False}),
    ]), navigate=False, timeout_ms=2000)
    assert execution["status"] == "success", json.dumps(execution["steps"], ensure_ascii=False)


def test_e2e_failing_count_reports_diagnostic(browser_page):
    execution = replay_trace(browser_page, _e2e_trace([
        ("#rows tr", {"assertion": "count", "expected": 9}),
    ]), navigate=False, timeout_ms=500)
    assert execution["status"] == "failed"
    dump = json.dumps(execution, ensure_ascii=False)
    assert "count" in dump

# ────────────────────────── D1：读取异常不得进入比较 ──────────────────────────
#
# 验收反例：count() 抛错时按 -1 参与比较，「至多 0 个」假通过；
# input_value()/get_attribute() 抛错时按异常字符串参与比较，
# not_equals / is_not_blank 假通过。错误是诊断信息，不是观测值。


class _BrokenLocator:
    """detached 元素：每次读取都抛错。"""

    def count(self):
        raise RuntimeError("detached")

    def input_value(self):
        raise RuntimeError("detached")

    def get_attribute(self, name):
        raise RuntimeError("detached")

    def inner_text(self):
        raise RuntimeError("detached")


def test_d1_helpers_fail_when_read_throws():
    broken = _BrokenLocator()
    # count：读不到就不存在「-1 ≤ 0」的假通过
    with pytest.raises(AssertionError, match="detached"):
        expect_count(broken, 0, "at_most", timeout=0.05)
    # value：异常字符串 != "expected" 不构成 not_equals 通过
    with pytest.raises(AssertionError, match="detached"):
        expect_value(broken, "expected", "not_equals", timeout=0.05)
    # attribute：异常字符串非空不构成 is_not_blank 通过
    with pytest.raises(AssertionError, match="detached"):
        expect_attribute(broken, "href", "", "is_not_blank", timeout=0.05)
    # equals 场景：消息保留最后的读取异常（诊断），但按失败处理
    with pytest.raises(AssertionError, match="detached"):
        expect_value(broken, "x", timeout=0.05)


def test_d1_replay_count_branch_fails_on_read_error():
    class P:
        def goto(self, url):
            pass

        def locator(self, selector):
            return _BrokenLocator()

    execution = replay_trace(P(), _trace(
        {"assertion": "count", "expected": 0, "op": "at_least"}),
        navigate=False, timeout_ms=150)
    assert execution["status"] == "failed"
    dump = json.dumps(execution, ensure_ascii=False)
    assert "detached" in dump      # 最后异常留在诊断里


def test_d1_replay_value_not_equals_fails_on_read_error():
    class P:
        def goto(self, url):
            pass

        def locator(self, selector):
            return _BrokenLocator()

    execution = replay_trace(P(), _trace(
        {"assertion": "value", "expected": "expected", "op": "not_equals"}),
        navigate=False, timeout_ms=150)
    assert execution["status"] == "failed"
    assert "detached" in json.dumps(execution, ensure_ascii=False)
