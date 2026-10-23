"""2026-10-23 评审轮新增：matches_assert / expect_* 断言语义边界。

范围与归属（评审轮 2026-10-23，HEAD 9f99671）：
- 本文件只增测试，不改任何生产代码；
- 「现状」用例：钉住当前行为并注明依据，防止无意识漂移；
- 「xfail(strict)」用例：断言**文档承诺**的语义，当前实现未满足 ——
  每条都写清现状与证据位置；生产代码修复后这些用例会 XPASS，
  届时应删除 xfail 标记并核对修复方向（见评审报告
  REDACTED-LOCAL-PATH/ai-projects/doc/cloud-recorder-review-20261023.md）。
- 修复批注（2026-10-23）：F1（expect_count 负数防线）与 F6（_match 的
  _AnyOf 失配只打类型/形状、不回显实际值）已在 assets/rec_assert.py
  落地，对应 xfail 已转普通通过；其余「现状」用例仍只钉行为。
- 修复批注（2026-10-23 第二批）：F4（matches_assert 入口 expected=None→""
  统一归一）与 F8（模块头注释对齐 matches 原始文本语义）已落地，本文件
  5 条 xfail(strict) 转普通通过（断言主体未放宽），并新增
  TestNoneExpectedEmptyContract 正反例矩阵钉住「expected=None ≡ ""」。
- 全部纯 Python / 假 locator，不依赖浏览器，可无限重复用于稳定性验证。
"""

import re
from datetime import datetime, timedelta

import pytest

import rec_assert
from rec_assert import (
    ANY_NUM, ANY_STR, assert_subset, expect_attribute, expect_count,
    expect_value, local_time_candidates, matches_assert, matches_local_time,
    matches_time_range, matches_time_window, parse_time_range, poll_until,
)


# ── 基础算子边界（现状钉住） ─────────────────────────────────────────


class TestMatchesAssertCurrentBehavior:
    def test_numeric_ops_reject_bool_and_none(self):
        # bool/None 拿不到数值就是失败，不参与字典序比较，也不按 1/0 混入
        assert matches_assert(True, 0, "greater_than") is False
        assert matches_assert(True, 1, "at_least") is False
        assert matches_assert(None, 0, "greater_than") is False
        assert matches_assert(None, "", "equals") is True   # 非 strict：None ≈ ""

    def test_numeric_underscore_string_parses_as_number(self):
        # 现状（Python 行为）：float("1_000") == 1000.0 —— 下划线分组被接受。
        # 钉住：这不是断言层引入的语义，但输入框里出现 "1_000" 时按 1000 比。
        assert matches_assert("1_000", 999, "greater_than") is True

    def test_numeric_nan_never_satisfies(self):
        # 数值算子下 nan 与任何数比较都是 False（float 语义）
        assert matches_assert("nan", 0, "greater_than") is False
        assert matches_assert("nan", 0, "at_most") is False
        # 现状：equals 是字符串比较，"nan" == "nan" 通过 —— 数值解析
        # 只发生在 greater/less/at_least/at_most，equals 不解析数值
        assert matches_assert("nan", "nan", "equals") is True

    def test_numeric_inf_satisfies_greater(self):
        assert matches_assert("9e999", 0, "greater_than") is True

    def test_strict_blank_uses_strip_not_collapse(self):
        # strict（value/attribute）：is_blank 只 strip 首尾，内部空白不归一
        assert matches_assert("  ", None, "is_blank", strict=True) is True
        assert matches_assert("\t\n", None, "is_not_blank", strict=True) is False
        # 非 strict 文本路径：连续空白压成一个后仍然空
        assert matches_assert("  ", None, "is_blank") is True

    def test_none_vs_empty_attribute_distinction(self):
        # D4 契约：属性缺失(None) ≠ 属性空("")
        assert matches_assert(None, "", "equals", strict=True) is False
        assert matches_assert("", "", "equals", strict=True) is True
        # F4 修复后：expected=None 在入口归一为 ""，与 expected="" 同判 ——
        # 缺失属性(None) 对「等于空」命题仍判 False（缺失 ≠ 空，D4 保持）
        assert matches_assert(None, None, "equals", strict=True) is False
        # 非 strict 文本路径两者同判（None 归一为 ""）
        assert matches_assert(None, "", "equals") is True

    def test_strict_not_contains_on_missing_attribute_passes(self):
        # 文档化的 strict 语义：缺失(None) 按「不含」处理
        assert matches_assert(None, "secret", "not_contains", strict=True) is True
        assert matches_assert("has secret", "secret", "not_contains",
                              strict=True) is False

    def test_unicode_not_normalized_beyond_whitespace(self):
        # 全角字符不做归一化 —— 相等就是逐字符相同
        assert matches_assert("\uff41\uff42\uff43", "abc", "equals") is False
        # 现状：不做 Unicode NFC/NFD 折叠 —— 组合形式 (e + U+0301) 与
        # 预组合 (U+00E9) 视为不同文本。钉住该行为（无隐藏归一化）。
        assert matches_assert("cafe\u0301", "caf\u00e9", "equals") is False
        assert matches_assert("caf\u00e9", "caf\u00e9", "equals") is True
        # 连续空白压成一个，不删除
        assert matches_assert("管  理  员", "管 理 员", "equals") is True
        assert matches_assert("管 理 员", "管理员", "equals") is False
    def test_regex_metachars_are_literal_in_contains(self):
        assert matches_assert("a.b", "a.b", "contains") is True
        assert matches_assert("aXb", "a.b", "contains") is False

    def test_bad_regex_fails_closed_both_paths(self):
        assert matches_assert("abc", "([unclosed", "matches") is False
        assert matches_assert("abc", "([unclosed", "matches", strict=True) is False

    def test_unknown_op_raises_both_paths(self):
        with pytest.raises(ValueError, match="不支持的比较算子"):
            matches_assert("a", "a", "regex_equals")
        with pytest.raises(ValueError, match="不支持的比较算子"):
            matches_assert("a", "a", "regex_equals", strict=True)

    def test_expected_none_nonstrict_string_ops_treat_as_empty(self):
        # docstring：「expected 为 None 一律按空串处理」—— 非 strict 的
        # equals/contains/starts_with 确实如此（经 _norm）。
        assert matches_assert("abc", None, "contains") is True      # "" in "abc"
        assert matches_assert("abc", None, "starts_with") is True
        assert matches_assert("", None, "equals") is True
        assert matches_assert("abc", None, "equals") is False


class TestStrictNoneExpectedContract:
    """matches_assert docstring「expected 为 None 一律按空串处理」——F4 已修复。

    修复（assets/rec_assert.py，2026-10-23 第二批）：expected=None 在
    matches_assert 入口（strict/非 strict 分派之前）统一归一为 ""，全部
    算子、两条路径看到同一个期望值。以下 3 条原 xfail(strict) 转普通通过
    （断言主体未放宽）；统一契约的正反例矩阵见 TestNoneExpectedEmptyContract。
    """

    @pytest.mark.parametrize("op", ["contains", "starts_with", "ends_with"])
    def test_strict_text_ops_treat_none_expected_as_empty(self, op):
        # F4 修复回归（原 xfail(strict)，2026-10-23 转正）：
        # contains("")/startswith("")/endswith("") 对任何非缺失文本为 True
        assert matches_assert("abc", None, op, strict=True) is True

    def test_matches_none_expected_treated_as_empty_pattern(self):
        # F4 修复回归（原 xfail(strict)）：空模式只匹配空串
        assert matches_assert("", None, "matches") is True

    def test_strict_not_equals_none_expected_follows_empty_semantics(self):
        # F4 修复回归（原 xfail(strict)）：空 == 空 → not_equals 判 False
        assert matches_assert("", None, "not_equals", strict=True) is False


class TestNoneExpectedEmptyContract:
    """F4 修复后的统一契约矩阵：expected=None 与 expected="" 完全同判。

    入口归一化后全部算子（strict 与非 strict）对两种 expected 的判定
    必须逐格一致 —— expected 侧参数化同断言钉住「完全一致」，actual 侧
    覆盖缺失(None)/空串/字面 "None"/非空文本。另钉：非 strict 文本算子
    的空白折叠不回退、matches 的原始（不折叠）语义、数值算子
    fail-closed、is_blank/is_not_blank 行为不变。
    """

    # strict 精确路径：expected ∈ {None, ""} × actual ∈ {None, "", "None", 非空}
    # 期望值逐格标注（D4：actual 缺失(None) ≠ 空("")，缺失对「等于空」判 False）
    @pytest.mark.parametrize("expected", [None, ""],
                             ids=["exp-none", "exp-empty"])
    @pytest.mark.parametrize("op,actual,want", [
        ("equals", None, False),
        ("equals", "", True),
        ("equals", "None", False),
        ("equals", "abc", False),
        ("not_equals", None, True),
        ("not_equals", "", False),
        ("not_equals", "None", True),
        ("not_equals", "abc", True),
        ("contains", None, False),
        ("contains", "", True),
        ("contains", "None", True),
        ("contains", "abc", True),
        ("not_contains", None, True),
        ("not_contains", "", False),
        ("not_contains", "None", False),
        ("not_contains", "abc", False),
        ("starts_with", None, False),
        ("starts_with", "", True),
        ("starts_with", "None", True),
        ("starts_with", "abc", True),
        ("ends_with", None, False),
        ("ends_with", "", True),
        ("ends_with", "None", True),
        ("ends_with", "abc", True),
        ("matches", None, False),
        ("matches", "", True),
        ("matches", "None", False),
        ("matches", "abc", False),
    ])
    def test_strict_matrix(self, op, actual, want, expected):
        assert matches_assert(actual, expected, op, strict=True) is want

    # 非 strict matches：expected=None ≡ ""（空模式只匹配空串），原始文本
    @pytest.mark.parametrize("expected", [None, ""],
                             ids=["exp-none", "exp-empty"])
    @pytest.mark.parametrize("actual,want", [
        (None, False),    # 原始文本 str(None)="None"，不匹配空模式
        ("", True),
        ("None", False),  # 字面 "None" 不匹配空模式
        ("abc", False),
        ("   ", False),   # 原始语义：纯空白 ≠ 空串（不折叠）
    ])
    def test_nonstrict_matches_matrix(self, actual, want, expected):
        assert matches_assert(actual, expected, "matches") is want

    # 非 strict 文本算子（经 _norm）：expected 侧 None 与 "" 本就同判，不回退
    @pytest.mark.parametrize("expected", [None, ""],
                             ids=["exp-none", "exp-empty"])
    @pytest.mark.parametrize("op,actual,want", [
        ("equals", None, True),      # 双方归一为 ""（既有行为）
        ("equals", "", True),
        ("equals", "abc", False),
        ("contains", None, True),
        ("contains", "", True),
        ("contains", "abc", True),
        ("not_equals", "", False),
        ("not_equals", "abc", True),
        ("starts_with", "abc", True),
        ("ends_with", "abc", True),
    ])
    def test_nonstrict_text_matrix(self, op, actual, want, expected):
        assert matches_assert(actual, expected, op) is want

    def test_nonstrict_text_ops_collapse_whitespace_pinned(self):
        # 空白折叠：非 strict 文本算子折叠连续空白（既有行为不回退）
        assert matches_assert("管  理  员 🎉", "管 理 员 🎉", "equals") is True
        assert matches_assert("a  b  c", "b  c", "contains") is True
        # matches 不折叠（原始语义，与 toHaveText(re) 的有意差异 —— F8 注释）
        assert matches_assert("line1\nline2", "line1 line2", "matches") is False
        assert matches_assert("a  b", "a b", "matches") is False
        assert matches_assert("a b", "a b", "matches") is True

    def test_missing_actual_with_none_expected_safe_and_definite(self):
        # 缺失 actual（None）× expected=None：不抛异常，结论明确
        assert matches_assert(None, None, "equals") is True
        assert matches_assert(None, None, "equals", strict=True) is False  # D4
        assert matches_assert(None, None, "contains", strict=True) is False
        assert matches_assert(None, None, "not_contains", strict=True) is True
        assert matches_assert(None, None, "matches") is False
        assert matches_assert(None, None, "is_blank") is True
        assert matches_assert(None, None, "is_not_blank", strict=True) is False

    def test_unicode_with_none_expected(self):
        # 中文/emoji 与 None 组合：空期望 ≡ 空串期望
        assert matches_assert("管理员 🎉", None, "contains", strict=True) is True
        assert matches_assert("管理员 🎉", "", "contains", strict=True) is True
        assert matches_assert("管理员 🎉", None, "equals", strict=True) is False
        assert matches_assert("管理员", None, "not_equals", strict=True) is True
        assert matches_assert("🎉", None, "matches") is False
        assert matches_assert("", None, "matches") is True

    @pytest.mark.parametrize("op", ["greater_than", "less_than",
                                    "at_least", "at_most"])
    def test_numeric_ops_none_expected_fail_closed(self, op):
        # 数值算子 expected=None：归一为 "" 后 float("") 仍解析失败 → False
        # （fail-closed 保持：不抛异常、不假通过）
        assert matches_assert("5", None, op) is False
        assert matches_assert(5, None, op) is False
        assert matches_assert(None, None, op) is False

    def test_blank_ops_unchanged_under_none_expected(self):
        # is_blank/is_not_blank 忽略 expected：None 期望下行为与修复前一致
        assert matches_assert("  ", None, "is_blank", strict=True) is True
        assert matches_assert("\t\n", None, "is_blank", strict=True) is True
        assert matches_assert("x", None, "is_blank", strict=True) is False
        assert matches_assert("", None, "is_not_blank", strict=True) is False
        assert matches_assert("x", None, "is_not_blank", strict=True) is True
        assert matches_assert("", None, "is_blank") is True
        assert matches_assert("  x ", None, "is_not_blank") is True
        assert matches_assert("   ", None, "is_not_blank") is False


# ── 时间区间边界 ─────────────────────────────────────────────────────


class TestTimeRangeEdges:
    @pytest.mark.parametrize("bad", [0, -1, True, False, 1.5, "5", 86401,
                                     2**32])
    def test_bad_duration_raises(self, bad):
        # None 是合法缺省（按起点粒度取 60/1 秒），不在非法之列；
        # 非法形状（bool/float/str/越界）一律 ValueError。
        with pytest.raises(ValueError, match="time_range_invalid_duration"):
            parse_time_range("10:20:00", bad)

    def test_duration_boundaries_accepted(self):
        assert parse_time_range("10:20:00", 1) == (10 * 3600 + 20 * 60, 1)
        assert parse_time_range("10:20:00", 86400) == (37200, 86400)

    def test_default_duration_by_granularity(self):
        # HH:MM → 60s；HH:MM:SS → 1s
        assert parse_time_range("10:20")[1] == 60
        assert parse_time_range("10:20:15")[1] == 1

    def test_zero_and_negative_prefix_rejected(self):
        for bad in ("0:20", "24:00", "10:60", "-1:20", "10:20:60", ""):
            with pytest.raises(ValueError, match="time_range_invalid_prefix"):
                parse_time_range(bad, 1)

    def test_none_text_and_non_string_text_are_safe(self):
        assert matches_time_range(None, "10:20") == []
        assert matches_time_range(12345, "10:20") == []
        # 现状：非字符串文本经 str() 强转后扫记号 —— 列表里恰好有
        # 时间形状的字符串会被命中（宽松行为，钉住不漂移）
        assert matches_time_range(["10:20"], "10:20") == ["10:20"]
        assert matches_time_range(["没有记号"], "10:20") == []

    def test_multiple_tokens_returned_in_order(self):
        hits = matches_time_range("10:20:59 之前 10:21:30 之后", "10:20:30", 60)
        # 起点 10:20:30，时长 60s → [10:20:30, 10:21:30)：
        # 10:20:59 在内，10:21:30 是终点（半开）算外
        assert hits == ["10:20:59"]

    def test_date_like_numbers_not_eaten(self):
        # 日期 2026-10-23、数量 10:20 之外的数字组合不能误判成时间记号
        assert matches_time_range("2026-10-23 订单 1020 件", "10:20") == []
        assert matches_time_range("编号 110:20 尾号", "10:20") == []  # 前面有数字

    def test_single_digit_second_is_lenient(self):
        # 现状：秒位单个数字也接受（(?:[0-5]\d|\d)）。钉住宽松行为。
        assert matches_time_range("检查时间 10:20:5", "10:20:00", 60) == ["10:20:5"]

    def test_full_day_window_wraps_midnight(self):
        assert matches_time_range("23:59:59 与 00:00:01", "23:59:59", 86400)
        assert len(matches_time_range("23:59:59 与 00:00:01",
                                      "23:59:59", 86400)) == 2


class TestTimeWindowEdges:
    def test_fluctuation_zero_is_exact_prefix_window(self):
        assert matches_time_window("10:20:59", "10:20", fluctuation_minutes=0)
        assert not matches_time_window("10:21:00", "10:20", fluctuation_minutes=0)

    def test_negative_fluctuation_is_no_window_extension(self):
        # 现状：负波动 max(0.0, x) 后 extra<=0 直接 False —— 前缀路径仍生效
        assert matches_time_window("10:20:00", "10:20", fluctuation_minutes=-5)
        assert not matches_time_window("10:21:00", "10:20", fluctuation_minutes=-5)

    def test_fluctuation_non_numeric_fails_closed(self):
        assert not matches_time_window("10:21:00", "10:20", fluctuation_minutes="x")

    def test_empty_expected_fails_closed(self):
        assert not matches_time_window("10:20:00", "   ")

    def test_none_actual_fails_closed(self):
        assert not matches_time_window(None, "10:20")


class TestLocalTimeCandidates:
    @pytest.fixture()
    def frozen_clock(self, monkeypatch):
        fixed = datetime(2026, 10, 23, 1, 2, 3)

        class _FrozenDT(datetime):
            @classmethod
            def now(cls):
                return fixed

        monkeypatch.setattr(rec_assert, "datetime", _FrozenDT)
        return fixed

    def test_zero_and_negative_slack_single_candidate(self, frozen_clock):
        assert local_time_candidates("%H:%M:%S", slack_seconds=0) == ["01:02:03"]
        assert local_time_candidates("%H:%M:%S", slack_seconds=-5) == ["01:02:03"]

    def test_coarse_format_dedupes_candidates(self, frozen_clock):
        # 冻结在 01:02:03，90s 窗口回溯到 01:00:33 → 覆盖 01:02/01:01/01:00
        cands = local_time_candidates("%H:%M", slack_seconds=90)
        assert cands == ["01:02", "01:01", "01:00"]
        assert len(local_time_candidates("%H:%M:%S", slack_seconds=0)) == 1

    def test_matches_local_time_equals_normalizes(self, frozen_clock):
        assert matches_local_time("  01:02:03  ", "%H:%M:%S", match="equals")
        assert not matches_local_time("01:02:03 之后", "%H:%M:%S", match="equals")
        assert matches_local_time("01:02:03 之后", "%H:%M:%S", match="contains")

    def test_matches_local_time_none_safe(self, frozen_clock):
        assert matches_local_time(None, "%H:%M") is False


# ── 轮询语义（假 locator，覆盖 D1 读取异常路径） ─────────────────────


class _FlakyLocator:
    """前 fail_n 次读取抛错，之后返回 value。记录调用次数。"""

    def __init__(self, value, fail_n=0, error=None):
        self.value = value
        self.fail_n = fail_n
        self.calls = 0
        self._error = error or RuntimeError("boom")

    def input_value(self):
        self.calls += 1
        if self.calls <= self.fail_n:
            raise self._error
        return self.value

    get_attribute = input_value


class _AlwaysFailingLocator:
    def __init__(self, error=None):
        self.calls = 0
        self._error = error or RuntimeError("boom")

    def input_value(self):
        self.calls += 1
        raise self._error

    get_attribute = input_value

    def count(self):
        self.calls += 1
        raise self._error


class TestPollReadErrorsNeverBecomeObservations:
    """D1 核心红线：读取异常不是观测值，不得参与比较造成假通过。"""

    def test_value_recovers_within_budget(self):
        loc = _FlakyLocator("target", fail_n=2)
        assert expect_value(loc, "target", timeout=2.0) == "target"
        assert loc.calls == 3

    def test_value_changed_mid_retry_is_accepted(self):
        # 第 1 次读到旧值（不匹配），第 2 次读到新值 —— 轮询要接受变化
        loc = _FlakyLocator("new", fail_n=1)
        assert expect_value(loc, "new", timeout=2.0) == "new"

    def test_persistent_read_failure_fails_with_last_error(self):
        err = RuntimeError("element detached")
        loc = _AlwaysFailingLocator(err)
        with pytest.raises(AssertionError) as ei:
            expect_value(loc, "anything", timeout=0.3)
        message = str(ei.value)
        assert "读取持续失败" in message
        assert "element detached" in message  # 最后异常带进诊断信息
        assert loc.calls >= 2  # 预算内确实重试过

    def test_not_equals_cannot_pass_on_error_string(self):
        # D1 反例的 expect_value 版本：not_equals + 持续读取失败必须失败，
        # 而不是拿异常字符串当观测值「不等于目标」假通过。
        loc = _AlwaysFailingLocator(RuntimeError("TimeoutError: boom"))
        with pytest.raises(AssertionError, match="读取持续失败"):
            expect_value(loc, "目标值", op="not_equals", timeout=0.3)

    def test_attribute_read_failure_same_rule(self):
        loc = _AlwaysFailingLocator(RuntimeError("detached"))
        with pytest.raises(AssertionError, match="读取持续失败"):
            expect_attribute(loc, "href", "", op="is_not_blank", timeout=0.3)

    def test_count_read_failure_is_not_zero_at_expect_level(self):
        # D1 count 分支在 helper 层的红线：count() 抛错 ≠ 0 个，
        # at_most 0 不得因读取失败假通过。
        loc = _AlwaysFailingLocator(RuntimeError("detached"))
        with pytest.raises(AssertionError, match="读取持续失败"):
            expect_count(loc, 0, op="at_most", timeout=0.3)

    def test_count_recovers_within_budget(self):
        class _CountFlaky:
            def __init__(self):
                self.calls = 0

            def count(self):
                self.calls += 1
                if self.calls <= 2:
                    raise RuntimeError("detached")
                return 3

        loc = _CountFlaky()
        assert expect_count(loc, 3, timeout=2.0) == 3

    def test_timeout_zero_still_reads_once(self):
        loc = _FlakyLocator("x")
        with pytest.raises(AssertionError):
            expect_value(loc, "y", timeout=0.0)
        assert loc.calls == 1  # 至少尝试读取一次再判超时


class TestAssertSubsetRedaction:
    """敏感值脱敏边界：期望侧被 ANY_STR 替换后，失配消息不得回显实际值。

    F6（评审 2026-10-23，本批已修复）：_match 的 _AnyOf 失配分支只打
    类型/形状（list(len=N) / dict(len=N) / 类型名），不回显 actual 的值
    内容 —— 非字符串 secret（数字 PIN / None / 嵌套结构 / bytes）在
    ANY_STR 下必然失配，修复前会把真实凭据写进 AssertionError 并落入
    执行报告。字符串凭据仍被 ANY_STR 吸收（匹配即静默，安全对照保留）。
    非占位符的标量失配仍回显两边的值 —— 那是非敏感字段的既有调试行为，
    评审报告 §3 F6 界定不在本修复范围（有现状钉住用例）。
    """

    def test_mismatch_message_does_not_echo_actual_secret(self):
        # F6 修复回归（原 xfail(strict)，2026-10-23 修复后转普通通过）
        with pytest.raises(AssertionError) as ei:
            assert_subset({"password": 12345678}, {"password": ANY_STR})
        assert "12345678" not in str(ei.value)

    @pytest.mark.parametrize(
        "bad_actual, forbidden_fragments, required_fragment",
        [
            pytest.param(12345678, ["12345678"], "int", id="int-pin"),
            pytest.param(3.1415926, ["3.1415926"], "float", id="float"),
            # None 的值内容（str/repr 形态）是 "None"，而新消息里的类型名是
            # "NoneType"（含子串 "None"）—— 故禁止的是旧回显片段「实际 None」
            pytest.param(None, ["实际 None"], "NoneType", id="none"),
            pytest.param({"user": "alice", "token": "sk-live-9f8e7d6c"},
                         ["sk-live-9f8e7d6c", "alice"],
                         "dict(len=2)", id="nested-dict"),
            pytest.param(["tok-a", "tok-b"], ["tok-a", "tok-b"],
                         "list(len=2)", id="nested-list"),
            pytest.param(b"hunter2-bytes-secret", ["hunter2-bytes-secret"],
                         "bytes", id="bytes"),
        ])
    def test_mismatch_matrix_does_not_echo_actual_values(
            self, bad_actual, forbidden_fragments, required_fragment):
        # 失配不回显矩阵：任何形状的实际值在 ANY_STR 失配时，消息只含
        # 类型/形状，绝不含值内容
        with pytest.raises(AssertionError) as ei:
            assert_subset({"password": bad_actual}, {"password": ANY_STR})
        message = str(ei.value)
        for fragment in forbidden_fragments:
            assert fragment not in message
        assert required_fragment in message
        assert "ANY_STR" in message  # 期望侧占位符标签在消息里（本身无值可泄）

    def test_string_secret_matched_by_any_str_stays_silent(self):
        # 现状红线之一：字符串凭据被 ANY_STR 吸收，根本不产生失配消息
        assert_subset({"password": "hunter2"}, {"password": ANY_STR})

    @pytest.mark.parametrize("secret", ["", "p@ss w0rd 中文", "12345678"])
    def test_more_string_secrets_absorbed_by_any_str(self, secret):
        # 安全对照扩展：空串/带空白与中文/纯数字**字符串**均按类型吸收
        # ——「12345678」作为字符串也不失配（ANY_STR 只比类型不比值）
        assert_subset({"password": secret, "nested": {"pw": secret}},
                      {"password": ANY_STR, "nested": {"pw": ANY_STR}})

    def test_any_num_mismatch_also_type_only(self):
        # 数值占位符失配同样只打类型：实际是字符串时不得回显字符串内容
        with pytest.raises(AssertionError) as ei:
            assert_subset({"port": "abc"}, {"port": ANY_NUM})
        message = str(ei.value)
        assert "'abc'" not in message and '"abc"' not in message
        assert "str" in message
        assert "ANY_NUM" in message
        # exclude_bool 边界：bool 对 ANY_NUM 失配，消息也只有类型名
        with pytest.raises(AssertionError) as ei_bool:
            assert_subset({"port": True}, {"port": ANY_NUM})
        assert "True" not in str(ei_bool.value)
        assert "bool" in str(ei_bool.value)

    def test_non_placeholder_scalar_mismatch_still_echoes(self):
        # 现状钉住（评审 §3 F6 界定不在修复范围）：期望侧是具体字面量
        # （未被占位符替换、非敏感字段）时，失配消息仍回显两边的值 ——
        # 这是既有调试行为，本钉防止未来无意识漂移，也防止误把占位符
        # 分支的脱敏扩大到全部标量。
        with pytest.raises(AssertionError) as ei:
            assert_subset({"retries": 3}, {"retries": 5})
        message = str(ei.value)
        assert "实际 3" in message
        assert "期望 5" in message

    def test_legal_match_including_placeholder_hits_has_no_problems(self):
        # 正对照：占位符命中（字符串/数值/嵌套内字段）不产生任何 problems
        assert_subset(
            {
                "id": "b3f1c2d4-0000-4000-8000-000000000001",
                "count": 42,
                "ratio": 1.5,
                "password": "hunter2",
                "nested": {"token": "abc", "n": 7},
                "tags": ["a", "b"],
            },
            {
                "id": ANY_STR,
                "count": ANY_NUM,
                "ratio": ANY_NUM,
                "password": ANY_STR,
                "nested": {"token": ANY_STR, "n": ANY_NUM},
                "tags": ["a", "b"],
            },
        )
        # 更强的直接对照：_match 对占位符命中的字段不追加任何 problems
        problems: list[str] = []
        rec_assert._match({"k": "v", "n": 1}, {"k": ANY_STR, "n": ANY_NUM},
                          "$", problems)
        assert problems == []


class TestPollUntil:
    def test_poll_returns_when_value_reaches_expected(self):
        box = {"v": 0}

        def fn():
            box["v"] += 1
            return box["v"]

        assert poll_until(fn, 3, timeout=2.0) == 3

    def test_poll_propagates_fn_exceptions(self):
        # 现状（与回放链的差异来源）：poll_until 不捕获 fn 的异常 ——
        # 生成的 starts_with/matches 用例在元素缺失时以 playwright
        # TimeoutError 直接冒泡（error），而回放链是干净的 AssertionError。
        # 该差异在生成-回放一致性文件里做两链对照。
        def fn():
            raise RuntimeError("inner boom")

        with pytest.raises(RuntimeError, match="inner boom"):
            poll_until(fn, True, timeout=0.3)


# ── 负数计数防线（2026-10-23 监督独立确认项的本侧复现） ────────────────


class _FixedCountLocator:
    """count() 返回固定值（用于契约层验证，非 Playwright 形状）。"""

    def __init__(self, value):
        self._value = value

    def count(self):
        return self._value


class TestNegativeCountGuard:
    """expect_count 的负数假通过（独立确认 2026-10-23）。

    历史 D1 缺陷的通道是「count() 读取失败喂 -1 进比较 → at_most 0 假通过」。
    本轮 HEAD 已堵住“读取抛错”路径，但**负数观测值**仍无防线：
    matches_assert(-1, 0, "at_most") 为 True；expect_count(expected=-1)
    也被接受（只拒 bool/非整数）。Playwright locator.count() 现网不产生
    负数，但 maa-fw/桌面 runner 与 mock reader 可以 —— 契约上必须堵。

    期望：count 读取层把负数观测值视为无效读数（断言失败，带诊断）；
    expected < 0 在入口即 ValueError（与 bool 同类）。
    """

    def test_negative_actual_count_fails_not_passes(self):
        # F1 修复（2026-10-23 第一批）：负数观测值不进比较，预算内重试，
        # 超时带值失败 —— at_most 0 不再被 -1 假通过。
        with pytest.raises(AssertionError, match="非法负数计数.*-1"):
            expect_count(_FixedCountLocator(-1), 0, op="at_most", timeout=0.3)

    def test_negative_expected_count_rejected(self):
        with pytest.raises(ValueError, match="非负整数"):
            expect_count(_FixedCountLocator(0), -1, op="at_least", timeout=0.3)

    def test_zero_and_positive_counts_still_pass(self):
        # 正常对照：0 与正数仍是合法观测/期望（修复不得误伤）
        assert expect_count(_FixedCountLocator(0), 0, op="at_most",
                            timeout=0.5) == 0
        assert expect_count(_FixedCountLocator(3), 1, op="at_least",
                            timeout=0.5) == 3
        assert expect_count(_FixedCountLocator(2), 2, op="equals",
                            timeout=0.5) == 2

    def test_negative_count_recovers_when_reader_turns_valid(self):
        # 负数是无效读数而非终局：预算内转正后照常通过
        class _NegThenValid:
            def __init__(self):
                self.calls = 0

            def count(self):
                self.calls += 1
                return -1 if self.calls <= 2 else 3

        assert expect_count(_NegThenValid(), 3, timeout=2.0) == 3

    def test_value_path_negative_numbers_unaffected(self):
        # F1 边界：value 路径的合法负数不得被全局禁掉（防线只在 count 层）
        class _NegValue:
            def input_value(self):
                return "-0.5"

        assert expect_value(_NegValue(), 0, op="less_than", timeout=0.5) == "-0.5"
        assert matches_assert("-5", -1, "greater_than") is False
        assert matches_assert(-0.5, 0, "less_than") is True

    def test_current_pure_function_semantics_pinned(self):
        # 现状钉住（纯函数层无读取上下文，不在此修）：
        # 数值算子按 float 语义接受负数 —— 输入值（value）路径上负数是
        # 合法观测（如温度 -5），所以防线必须放在 count 读取层而不是
        # matches_assert 全局。此钉防止未来误修成全局禁负。
        assert matches_assert(-1, 0, "at_most") is True
        assert matches_assert(0, -1, "at_least") is True
        assert matches_assert(-5, -1, "greater_than") is False
        assert matches_assert(-0.5, 0, "less_than") is True
