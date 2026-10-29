"""生成的 pytest 用例要用的三个小工具。

JS 版生成器靠 @playwright/test 的 expect 提供了三样东西，Python 版**都没有**：

    toMatchObject(...)     → 这里的 assert_subset
    expect.any(String)     → 这里的 ANY_STR
    expect.poll(fn).toBe() → 这里的 poll_until

所以生成的用例要 `from rec_assert import ...`。把它们放在一个文件里，
是为了让生成的用例只有一个额外依赖，拷到别的项目里也带得走。
"""

import re
import time
from datetime import datetime, timedelta
from typing import Any


class _AnyOf:
    """占位符：只比类型，不比值。

    请求体里的 UUID、雪花 ID、时间戳每次运行都不一样，整条删掉又丢了
    「这个字段必须存在」的信息。用它换掉具体值，字段在不在、类型对不对
    仍然被守住。
    """

    __slots__ = ("_type", "_label", "_exclude_bool")

    def __init__(self, type_: type, label: str, *, exclude_bool: bool = False):
        self._type = type_
        self._label = label
        self._exclude_bool = exclude_bool

    def __eq__(self, other: Any) -> bool:
        if self._exclude_bool and isinstance(other, bool):
            return False
        return isinstance(other, self._type)

    def __hash__(self):
        return hash(self._label)

    def __repr__(self) -> str:
        return self._label


ANY_STR = _AnyOf(str, "ANY_STR")
ANY_NUM = _AnyOf((int, float), "ANY_NUM", exclude_bool=True)


def _match(actual: Any, expected: Any, path: str, problems: list[str]) -> None:
    if isinstance(expected, _AnyOf):
        if expected != actual:
            # F6（评审 2026-10-23）：占位符（ANY_STR/ANY_NUM）失配只打类型/形状。
            # 录制期标记 secret 的字段在期望侧被换成占位符（「只比类型不比值」），
            # 走到这里时 actual 往往就是真实凭据；非字符串实际值（数字 PIN、
            # None、嵌套结构、bytes）对占位符必然失配，值一旦回显就会写进
            # AssertionError 并落入执行报告。所以消息只保留类型与容器长度，
            # 任何分支都不出现 actual 的值内容。非占位符标量失配（下方分支）
            # 仍回显值 —— 那是非敏感字段的既有调试行为，不在本修复范围。
            if isinstance(actual, list):
                shape = f"list(len={len(actual)})"
            elif isinstance(actual, dict):
                shape = f"dict(len={len(actual)})"
            else:
                shape = type(actual).__name__
            problems.append(f"{path}: 期望 {expected}，实际类型 {shape}")
        return
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            problems.append(f"{path}: 期望对象，实际 {type(actual).__name__}")
            return
        for k, v in expected.items():
            if k not in actual:
                problems.append(f"{path}.{k}: 字段缺失")
            else:
                _match(actual[k], v, f"{path}.{k}", problems)
        return
    if isinstance(expected, list):
        if not isinstance(actual, list):
            problems.append(f"{path}: 期望数组，实际 {type(actual).__name__}")
            return
        # 数组按长度和逐项比：录制下来的数组长度本身就是契约的一部分
        if len(actual) != len(expected):
            problems.append(f"{path}: 长度 {len(actual)}，期望 {len(expected)}")
            return
        for i, v in enumerate(expected):
            _match(actual[i], v, f"{path}[{i}]", problems)
        return
    if actual != expected:
        problems.append(f"{path}: 期望 {expected!r}，实际 {actual!r}")


def assert_subset(actual: Any, expected: Any) -> None:
    """断言 actual 里**包含** expected 描述的结构（对象按子集比，数组按全量比）。

    对应 JS 的 toMatchObject：多出来的字段不算失败 —— 后端加字段不该让
    既有用例挂掉，少字段和改值才该挂。
    """
    problems: list[str] = []
    _match(actual, expected, "$", problems)
    if problems:
        raise AssertionError("请求体与录制时的形态不符:\n  " + "\n  ".join(problems))


def poll_until(fn, expected, timeout: float = 5.0, interval: float = 0.1):
    """反复调 fn 直到返回值等于 expected，超时则失败。

    对应 JS 的 expect.poll。开关这类"点完要等 UI 自己翻状态"的场景必须轮询，
    直接断言会在动画/请求还没回来的时候误判。
    """
    deadline = time.monotonic() + timeout
    last = object()
    while True:
        last = fn()
        if last == expected:
            return last
        if time.monotonic() >= deadline:
            raise AssertionError(f"等待 {timeout}s 后仍不等于 {expected!r}，最后一次是 {last!r}")
        time.sleep(interval)

# ────────────────────────── 运行时计算的期望值 ──────────────────────────
#
# 录制下来的 expected 是**那一刻**的字面量。页面上凡是显示时间的字段（「最近使用」
# 「更新于」），断字面量隔一会儿就红，而红的原因和被测功能无关。
#
# 所以这类断言的期望值要在**回放那一刻**算出来，而不是从录制里搬。

LOCALTIME_KIND = "localtime"
TIMEWINDOW_KIND = "timewindow"
# 前缀 + 时长的时间区间（HH:MM → 整分钟；HH:MM:SS + durationSeconds）。
TIMETRANGE_KIND = "timerange"
DEFAULT_TIME_FORMAT = "%H:%M"
# 跨分钟边界的容差：页面在 23:24:59 渲染、断言在 23:25:00 求值，
# 两边差一分钟却都没错。不给容差的话这种假红会随机出现，
# 而且复现不了 —— 比它要防的问题更难查。
DEFAULT_SLACK_SECONDS = 90


def local_time_candidates(fmt: str = DEFAULT_TIME_FORMAT,
                          slack_seconds: float = DEFAULT_SLACK_SECONDS) -> list[str]:
    """此刻往前 slack_seconds 内，按 fmt 格式化出的所有**不同**取值。

    只按格式化结果去重，所以 fmt 越粗（"%H:%M"）候选越少，通常就 1~2 个。
    不解析时间、不做时区换算 —— 纯粹是值的比对。
    """
    now = datetime.now()
    seen, out = set(), []
    step = 15.0
    offset = 0.0
    while offset <= max(0.0, slack_seconds):
        value = (now - timedelta(seconds=offset)).strftime(fmt)
        if value not in seen:
            seen.add(value)
            out.append(value)
        offset += step
    return out


def matches_local_time(actual, fmt: str = DEFAULT_TIME_FORMAT, *,
                       match: str = "contains",
                       slack_seconds: float = DEFAULT_SLACK_SECONDS) -> bool:
    """actual 里是不是就是此刻的时间。

    match="contains"（默认）：actual 含有该时间串。「2026-08-18 23:24:07」这种
        整段时间戳里找 "23:24" 用它。
    match="equals"：actual 恰好等于该时间串，两端空白按 Playwright 的规则归一化。
    """
    if actual is None:
        return False
    text = " ".join(str(actual).split())
    for candidate in local_time_candidates(fmt, slack_seconds):
        if (candidate in text) if match == "contains" else (text == candidate):
            return True
    return False


def expect_local_time(locator, fmt: str = DEFAULT_TIME_FORMAT, *,
                      match: str = "contains", read: str = "text",
                      slack_seconds: float = DEFAULT_SLACK_SECONDS,
                      timeout: float = 5.0) -> str:
    """断言元素显示的时间就是回放此刻的本机时间。

    read="text"（默认）读 inner_text；read="value" 读 input_value ——
    输入框的时间在 value 上，inner_text 恒为空，读错了断言永远不会通过。

    轮询期间**每次重算**期望值 —— 页面可能几秒后才刷新出新时间，
    拿一个固定下来的期望值去等，等到的会是过时的比较基准。
    """
    deadline = time.monotonic() + timeout
    actual = None
    last_error = None
    while True:
        try:
            actual = locator.input_value() if read == "value" else locator.inner_text()
            last_error = None
        except Exception as error:
            # 读取异常不进比较（错误不是观测值），预算内重试
            last_error = error
        if (last_error is None
                and matches_local_time(actual, fmt, match=match,
                                       slack_seconds=slack_seconds)):
            return actual
        if time.monotonic() >= deadline:
            if last_error is not None:
                raise AssertionError(
                    f"时间断言失败: 读取持续失败 期望{'含有' if match == 'contains' else '等于'}"
                    f"此刻时间 {local_time_candidates(fmt, slack_seconds)}（fmt={fmt!r}）: "
                    f"{type(last_error).__name__}: {last_error}"
                )
            raise AssertionError(
                f"时间断言失败: actual={actual!r} "
                f"期望{'含有' if match == 'contains' else '等于'}此刻时间 "
                f"{local_time_candidates(fmt, slack_seconds)}（fmt={fmt!r}）"
            )
        time.sleep(0.1)


_TIME_WINDOW_PATTERNS = {
    "%H:%M": (
        re.compile(r"(?<!\d)(\d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)(?!\d)"),
        ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"),
        timedelta(minutes=1),
    ),
    "%Y-%m-%d %H:%M": (
        re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}"
                   r"(?::\d{2}(?:\.\d{1,6})?)?)(?!\d)"),
        ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"),
        timedelta(minutes=1),
    ),
    "%Y-%m-%d": (
        re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)"),
        ("%Y-%m-%d",),
        timedelta(days=1),
    ),
}


def _parse_time_window_value(value: str, formats: tuple[str, ...]) -> datetime | None:
    value = value.replace("T", " ")
    for candidate_format in formats:
        try:
            return datetime.strptime(value, candidate_format)
        except ValueError:
            continue
    return None


def matches_time_window(actual, expected: str, fmt: str = DEFAULT_TIME_FORMAT, *,
                        fluctuation_minutes: float = 0) -> bool:
    """先按 expected 做前缀匹配，再应用可选的向后分钟波动。

    expected="10:20" 直接接受任何以 "10:20" 开头的显示值。波动为 1 时，
    再接受下一分钟 10:21:xx，而 10:22:00 不接受。
    """
    if actual is None or fmt not in _TIME_WINDOW_PATTERNS:
        return False
    text = " ".join(str(actual).split())
    expected_text = str(expected).strip()
    if not expected_text:
        return False
    if text.startswith(expected_text):
        return True
    pattern, actual_formats, base_width = _TIME_WINDOW_PATTERNS[fmt]
    expected_value = _parse_time_window_value(expected_text, (fmt,))
    if expected_value is None:
        return False
    try:
        extra = timedelta(minutes=max(0.0, float(fluctuation_minutes)))
    except (TypeError, ValueError):
        return False
    if extra <= timedelta(0):
        return False
    end = expected_value + base_width + extra
    # 波动比较也只接受从字符串开头读出的时间；否则会把
    # "旧记录 10:21" 这种带说明前缀的内容误当成 expected 的前缀延伸。
    for match in pattern.finditer(text):
        if match.start() != 0:
            continue
        candidate = _parse_time_window_value(match.group(1), actual_formats)
        if candidate is None:
            continue
        # 只有纯时分格式可能跨午夜；把 00:xx 视为期望日的下一天。
        if fmt == "%H:%M" and end.date() > expected_value.date() and candidate < expected_value:
            candidate += timedelta(days=1)
        if expected_value <= candidate < end:
            return True
    return False


def expect_time_window(locator, expected: str, fmt: str = DEFAULT_TIME_FORMAT, *,
                       read: str = "text", fluctuation_minutes: float = 0,
                       timeout: float = 5.0) -> str:
    """轮询元素，直到其时间落入固定的 expected 时间窗口。"""
    deadline = time.monotonic() + timeout
    actual = None
    last_error = None
    while True:
        try:
            actual = locator.input_value() if read == "value" else locator.inner_text()
            last_error = None
        except Exception as error:
            # 读取异常不进比较（错误不是观测值），预算内重试
            last_error = error
        if (last_error is None
                and matches_time_window(
                    actual, expected, fmt, fluctuation_minutes=fluctuation_minutes
                )):
            return actual
        if time.monotonic() >= deadline:
            if last_error is not None:
                raise AssertionError(
                    f"时间范围断言失败: 读取持续失败 expected={expected!r}, fmt={fmt!r}: "
                    f"{type(last_error).__name__}: {last_error}"
                )
            raise AssertionError(
                f"时间范围断言失败: actual={actual!r}, expected={expected!r}, "
                f"fmt={fmt!r}, fluctuation_minutes={fluctuation_minutes!r}"
            )
        time.sleep(0.1)


def local_time_value(fmt: str = "%Y-%m-%d", *, offset_days: int = 0,
                     offset_seconds: int = 0) -> str:
    """按回放此刻的本机时钟算出一个值，用来填输入框。

    这是 expect_local_time 的输入侧对偶。日期筛选框里填死值（"2026-08-09"）
    的脚本不会报错，只会**悄悄查错区间**：录制那天它是「9 天前」，
    下个月回放就成了「40 天前」。

    offset_days 保留录制时那个日期与当天的相对关系 —— 意图是「近 N 天」
    就该按 N 天走，而不是钉在某一天。
    """
    moment = datetime.now() + timedelta(days=offset_days, seconds=offset_seconds)
    return moment.strftime(fmt)

# ────────────────────────── 断言比较算子 ──────────────────────────
#
# 以前只有「严格等于」一种比较：想守「错误消息包含 xxx」只能把整段文本
# 钉死（换个措辞就红），「列表里要有 3 条」「数值大于 5」根本表达不了。
#
# 算子集对齐成熟商用系统的通用面（Playwright expect 的 toHaveText/
# toContain/toHaveCount、Katalon 的 verify 关键字）：
#
#   * 非 strict 的字符串算子（equals/not_equals/contains/not_contains/
#     starts_with/ends_with）两边都经 _norm 归一化再比：连续空白压成一个
#     + None→""（对齐 to_have_text）；strict 路径（value/attribute）是精确
#     比较，不做空白归一化 ——「缺失(None)」与「空("")」必须可区分；
#   * matches 是正则 fullmatch（与 toHaveText(re) 一样是全文匹配，不是搜索），
#     但用**原始**文本：不做空白归一化（"line1\nline2" 原样参与匹配，e2e 有
#     反例钉住）—— 这是与 toHaveText(re)（匹配前先归一化）的**有意**差异；
#   * expected 为 None 一律按空串处理：在 matches_assert 入口统一归一
#     （两条路径、全部算子；matches 亦然 —— 空模式只匹配空串）；
#   * 数值算子（greater_than / less_than）只用于输入值：先解析成 float，
#     解析不出就是断言失败而不是字符串比较 —— 静默的字符串比较会给出
#     看起来对、其实一直在比字典序的「绿」；
#   * count 比的是 DOM 元素个数（locator.count()，隐藏元素也算）。
#     「不在 DOM」≠「存在但隐藏」：要断言看不见，用 visible 断言，不用 count。
#
# op 缺省 equals —— 老录制里没有这个字段，语义与旧版逐字节一致。

STRING_OPS = ("equals", "not_equals", "contains", "not_contains",
              "starts_with", "ends_with", "matches", "is_blank", "is_not_blank")
VALUE_OPS = STRING_OPS + ("greater_than", "less_than")
COUNT_OPS = ("equals", "at_least", "at_most")


def _norm(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def matches_assert(actual: Any, expected: Any, op: str = "equals",
                   *, strict: bool = False) -> bool:
    """纯函数比较：actual 在算子 op 下是否满足 expected。只返回布尔，不抛异常。

    is_blank / is_not_blank 不用 expected；其余算子里 expected 为 None
    一律按空串处理（equals 空串是合法命题，但菜单侧要求显式勾选「允许空值」）。
    未知算子抛 ValueError —— 宁可生成时报错，也不让回放静默地不比。

    strict=True 用于输入值 / 属性路径：**精确比较** —— 旧回放语义。
    text 才做空白归一化；value/attribute 不归一，且保留「属性不存在」
    （get_attribute 返回 None）与「属性为空」（""）的区别。显式扩展算子
    （包含/前后缀/正则）在 strict 路径按原始字符串比，actual=None 按
    「不含/不匹配」处理；expected=None 已在入口统一归一为 ""（F4）。
    """
    if expected is None:
        # F4（评审 2026-10-23）：把 docstring 承诺的「expected 为 None 一律
        # 按空串处理」落在入口 —— 在 strict/非 strict 分派之前统一归一，
        # 全部算子看到同一个期望值。is_blank/is_not_blank 忽略 expected，
        # 不受影响；数值算子两侧本就 fail-closed（float("None") 与 float("")
        # 同为 ValueError → False），判定不变；strict 的 equals/not_equals
        # 从「与 None 的原始比较」改为「与 "" 比较」—— expected 侧 None 与
        # "" 同判，actual 侧「缺失 vs 空」的区分不受影响。
        expected = ""
    if op in ("is_blank", "is_not_blank"):
        if strict:
            blank = actual is None or str(actual).strip() == ""
        else:
            blank = _norm(actual) == ""
        return blank if op == "is_blank" else not blank
    if op in ("greater_than", "less_than", "at_least", "at_most"):
        try:
            a, e = float(str(actual).strip()), float(str(expected).strip())
        except (TypeError, ValueError):
            return False
        if op == "greater_than":
            return a > e
        if op == "less_than":
            return a < e
        if op == "at_least":
            return a >= e
        return a <= e    # at_most
    if strict:
        # 精确路径：无空白归一化、无 None→"" 换算。
        if op == "equals":
            return actual == expected
        if op == "not_equals":
            return actual != expected
        text = None if actual is None else str(actual)
        if op == "contains":
            return text is not None and str(expected) in text
        if op == "not_contains":
            return text is None or str(expected) not in text
        if op == "starts_with":
            return text is not None and text.startswith(str(expected))
        if op == "ends_with":
            return text is not None and text.endswith(str(expected))
        if op == "matches":
            try:
                return (text is not None
                        and re.fullmatch(str(expected), text) is not None)
            except re.error:
                return False
        raise ValueError(f"不支持的比较算子: {op!r}")
    a, e = _norm(actual), _norm(expected)
    if op == "equals":
        return a == e
    if op == "not_equals":
        return a != e
    if op == "contains":
        return e in a
    if op == "not_contains":
        return e not in a
    if op == "starts_with":
        return a.startswith(e)
    if op == "ends_with":
        return a.endswith(e)
    if op == "matches":
        try:
            return re.fullmatch(str(expected), str(actual)) is not None
        except re.error:
            return False
    raise ValueError(f"不支持的比较算子: {op!r}")


def _poll_stable(read, satisfied, *, timeout: float, poll: float, stable: float,
                 make_error):
    """统一 deadline + stable 轮询核心，生成链与回放链共用（P0，§6）。

    read() 读取观测值，抛错即「本轮读取失败」—— 错误不是观测值，记录最后
    异常并继续到预算结束，绝不拿异常字符串去比较（否则 not_equals 会假通过）。
    条件满足后必须**连续保持** stable 秒才通过：负断言（不可见/count=0 等）默认
    带稳定窗口，避免「先缺席后出现」的中间态抢跑。
    """
    deadline = time.monotonic() + timeout
    last_actual = None
    last_error = None
    stable_since = None
    while True:
        try:
            last_actual = read()
            last_error = None
        except Exception as error:
            last_error = error
            stable_since = None
        ok = last_error is None and satisfied(last_actual)
        if ok:
            if stable <= 0:
                return last_actual
            if stable_since is None:
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= stable:
                return last_actual
        else:
            stable_since = None
        if time.monotonic() >= deadline:
            raise make_error(last_actual, last_error)
        time.sleep(poll)


def expect_value(locator, expected, op: str = "equals",
                 timeout: float = 5.0, *, poll: float = 0.1,
                 stable: float = 0.0) -> Any:
    """轮询输入框的 value 直到满足算子（精确语义，见 matches_assert strict）。

    轮询中途元素 detached 会让 input_value() 抛错 —— 错误是诊断信息，
    **不是观测值**：拿异常字符串参与比较会让 not_equals / is_not_blank
    用错误消息「通过」（假通过）。所以只在成功读取后才比较，读取失败
    在预算内重试，超时带最后异常明确失败。
    """
    def err(last, last_error):
        if last_error is not None:
            return AssertionError(
                f"输入值断言失败: 读取持续失败 op={op!r} expected={expected!r}: "
                f"{type(last_error).__name__}: {last_error}"
            )
        return AssertionError(
            f"输入值断言失败: op={op!r} expected={expected!r} actual={last!r}"
        )
    return _poll_stable(
        lambda: locator.input_value(),
        lambda v: matches_assert(v, expected, op, strict=True),
        timeout=timeout, poll=poll, stable=stable, make_error=err,
    )


def expect_attribute(locator, name: str, expected, op: str = "equals",
                     timeout: float = 5.0, *, poll: float = 0.1,
                     stable: float = 0.0) -> Any:
    """轮询元素的某个属性直到满足算子（精确语义）。

    属性不存在时 get_attribute 返回 None —— 精确比较下 None != ""，
    「断言 href 等于空」和「断言 href 不存在」因此可区分。
    读取抛错的处理同 expect_value：重试，超时带最后异常失败。
    """
    def err(last, last_error):
        if last_error is not None:
            return AssertionError(
                f"属性断言失败: {name} 读取持续失败 op={op!r} expected={expected!r}: "
                f"{type(last_error).__name__}: {last_error}"
            )
        return AssertionError(
            f"属性断言失败: {name} op={op!r} expected={expected!r} actual={last!r}"
        )
    return _poll_stable(
        lambda: locator.get_attribute(name),
        lambda v: matches_assert(v, expected, op, strict=True),
        timeout=timeout, poll=poll, stable=stable, make_error=err,
    )


def expect_count(locator, expected: int, op: str = "equals",
                 timeout: float = 5.0, *, poll: float = 0.1,
                 stable: float = 0.0) -> int:
    """断言匹配到的元素**个数**满足算子（Playwright to_have_count 只有等于）。

    at_least / at_most 是自加：「至少 1 条记录」「不超过 N 个候选」是商用
    系统里常见的数量断言。at_most 在 0 时也满足 —— 断「不超过 N」前请确认
    列表已经加载完（或再配一条内容断言），否则页面还没渲染就「通过」了。

    count() 抛错（元素集 detached）不是「0 个」：读取失败只重试，
    超时带最后异常失败 —— 否则 at_most 0 会拿 -1 假通过。
    负数观测值同理：count 的合法域是非负整数，读取路径给出负数
    （历史 D1 的 -1 形状）**不进比较**，预算内重试，超时带值失败；
    expected 为负在入口即 ValueError。输入值（value）路径的负数是
    合法观测，不受此限 —— 防线只在 count 层。
    """
    if (not isinstance(expected, int) or isinstance(expected, bool)
            or expected < 0):
        raise ValueError(
            f"count 断言的 expected 必须是非负整数: {expected!r}")
    def err(last, last_error):
        if last_error is not None:
            return AssertionError(
                f"数量(count)断言失败: 读取持续失败 op={op!r} expected={expected!r}: "
                f"{type(last_error).__name__}: {last_error}"
            )
        if last is not None and last < 0:
            return AssertionError(
                f"数量(count)断言失败: 读取到非法负数计数 actual={last!r} "
                f"op={op!r} expected={expected!r}"
                "（count 的合法域是非负整数）"
            )
        return AssertionError(
            f"数量(count)断言失败: op={op!r} expected={expected!r} actual={last!r}"
        )
    return _poll_stable(
        lambda: locator.count(),
        lambda v: _last_ok(v, expected, op),
        timeout=timeout, poll=poll, stable=stable, make_error=err,
    )


def _last_ok(actual, expected, op) -> bool:
    return actual >= 0 and matches_assert(actual, expected, op)


# ────────────────────────── 页面级断言（P1：URL / 标题） ─────────────────────
#
# 生成链与回放链共用同一套 deadline+stable 轮询与 matches_assert 比较，
# 与元素断言同核心。比较的是页面自身的 url / title 字符串（非 strict，作
# 空白归一化后按算子比 —— equals/contains/starts_with/matches 全走 matches_assert）。


def _page_value_err(kind, last, last_error, expected, op):
    if last_error is not None:
        return AssertionError(
            f"{kind} 断言失败: 读取持续失败 op={op!r} expected={expected!r}: "
            f"{type(last_error).__name__}: {last_error}"
        )
    return AssertionError(
        f"{kind} 断言失败: actual={last!r} expected={expected!r} op={op!r}"
    )


def expect_page_url(page, expected, op: str = "equals", *,
                    timeout: float = 5.0, poll: float = 0.1,
                    stable: float = 0.0) -> str:
    """轮询页面 URL 直到满足算子（页面级断言）。"""
    return _poll_stable(
        lambda: page.url,
        lambda u: matches_assert(u, expected, op),
        timeout=timeout, poll=poll, stable=stable,
        make_error=lambda last, le: _page_value_err(
            "页面URL", last, le, expected, op),
    )


def expect_page_title(page, expected, op: str = "equals", *,
                      timeout: float = 5.0, poll: float = 0.1,
                      stable: float = 0.0) -> str:
    """轮询页面标题直到满足算子（页面级断言）。"""
    return _poll_stable(
        lambda: page.title(),
        lambda t: matches_assert(t, expected, op),
        timeout=timeout, poll=poll, stable=stable,
        make_error=lambda last, le: _page_value_err(
            "页面标题", last, le, expected, op),
    )


def expect_page_count(page, expected: int, op: str = "equals", *,
                      timeout: float = 5.0, poll: float = 0.1,
                      stable: float = 0.0) -> int:
    """轮询当前 browser context 的 page 数直到满足算子（P2，§5.1 页面范围 page_count）。

    默认只统计当前 browser context（`page.context.pages`），不含其他 context。
    count 类算子走 matches_assert 的数值比较（equals/at_least/at_most）。
    读取失败（context detached 等）不是观测值：重试，超时带最后异常失败。
    """
    def err(last, last_error):
        if last_error is not None:
            return AssertionError(
                f"页面数量断言失败: 读取持续失败 op={op!r} expected={expected!r}: "
                f"{type(last_error).__name__}: {last_error}"
            )
        return AssertionError(
            f"页面数量断言失败: actual={last!r} expected={expected!r} op={op!r}"
        )
    return _poll_stable(
        lambda: len(page.context.pages),
        lambda n: matches_assert(n, expected, op),
        timeout=timeout, poll=poll, stable=stable, make_error=err,
    )


# ────────────────────────── 网络契约断言（P2） ─────────────────────────────────
#
# 最近请求选择器 + status / request_body / response_body subset，以及
# request_count=0 的有界观察窗（§5.1 网络范围、§6 负断言语义）。生成链与回放链
# 共用同一套比较语义（assert_subset / matches_assert + 统一 deadline/stable 轮询）。


def expect_response_status(response, expected: int, *, timeout: float = 5.0,
                           poll: float = 0.1, stable: float = 0.0) -> int:
    """轮询响应的 status 直到等于 expected（网络断言）。"""
    def err(last, last_error):
        if last_error is not None:
            return AssertionError(
                f"响应状态断言失败: 读取持续失败 expected={expected!r}: "
                f"{type(last_error).__name__}: {last_error}"
            )
        return AssertionError(
            f"响应状态断言失败: actual={last!r} expected={expected!r}"
        )
    return _poll_stable(
        lambda: response.status,
        lambda s: matches_assert(s, expected, "equals"),
        timeout=timeout, poll=poll, stable=stable, make_error=err,
    )


def matches_body_subset(actual: Any, expected: Any) -> bool:
    """body 用 subset 语义匹配：多出来的字段不算失败，少字段 / 改值才算。
    复用 assert_subset 的 _match 定义，只返回布尔不抛。"""
    problems: list[str] = []
    _match(actual, expected, "$", problems)
    return not problems


def request_count_zero(watcher, url_filter, start_t: float, end_t: float, *,
                       stable: float = 0.3, timeout: float = 2.0,
                       poll: float = 0.1) -> bool:
    """断言 [start_t, end_t) 观察窗内没有匹配 url_filter 的请求（§6 负断言语义）。

    watcher.count(url_filter) 在窗内统计请求数。**不得在动作刚发生时就判 0** ——
    请求可能还在路上；负断言带稳定窗口，只有「连续稳定地 0」才通过。
    窗由调用方（录制/生成）记录动作的起止边界，避免把窗外的自我请求算进来。
    """
    def count():
        return watcher.count(url_filter)
    def err(actual, last_error):
        if last_error is not None:
            return AssertionError(
                f"请求数为 0 断言失败: 读取持续失败: {type(last_error).__name__}: {last_error}"
            )
        return AssertionError(
            f"请求数为 0 断言失败: 窗 [{int(start_t)},{int(end_t)}) 内实际请求数 "
            f"actual={actual!r}（期望 0）"
        )
    _poll_stable(
        count,
        lambda n: int(n) == 0,
        timeout=timeout, poll=poll, stable=stable, make_error=err,
    )
    return True


# ────────────────────────── 布尔状态断言（P1：enabled/editable/selected 等） ──
#
# visible / checked / enabled / editable / selected 五类布尔状态统一走这一个
# 入口。生成链（生成 spec 里 emit expect_state(...)）与回放链（_assert_locator）
# 共用同一份轮询与稳定语义 —— 避免每条链各自写一遍 is_* 的 deadline 循环。

_STATE_READERS = {
    "visible": lambda loc: loc.is_visible(),
    "checked": lambda loc: loc.is_checked(),
    "enabled": lambda loc: loc.is_enabled(),
    "editable": lambda loc: loc.is_editable(),
    # Playwright Locator 没有 is_selected；option 的选中态从元素属性读取
    "selected": lambda loc: loc.evaluate("(el) => !!el.selected"),
}


def expect_state(locator, expected: bool, kind: str, *,
                 timeout: float = 5.0, poll: float = 0.1,
                 stable: float = 0.0) -> bool:
    """轮询某个布尔 UI 状态（visible/enabled/checked/editable/selected）直到
    等于 expected，满足后按需连续保持 stable 秒。

    expected=False（负断言）默认由调用方传入稳定窗口，防「先缺席后出现」抢跑；
    kind 必须是 _STATE_READERS 的键，未知立即 ValueError（不猜）。
    读取抛错不是观测值：重试，超时带最后异常明确失败。
    """
    reader = _STATE_READERS.get(kind)
    if reader is None:
        raise ValueError(f"不支持的布尔状态: {kind!r}")
    def err(last, last_error):
        if last_error is not None:
            return AssertionError(
                f"{kind} 状态断言失败: 读取持续失败 expected={expected!r}: "
                f"{type(last_error).__name__}: {last_error}"
            )
        return AssertionError(
            f"{kind} 状态断言失败: expected={expected!r} actual={last!r}"
        )
    return _poll_stable(
        lambda: reader(locator),
        lambda v: bool(v) is bool(expected),
        timeout=timeout, poll=poll, stable=stable, make_error=err,
    )


def expect_text(locator, expected, op: str = "equals", *,
                timeout: float = 5.0, poll: float = 0.1,
                stable: float = 0.0) -> str:
    """重新轮询元素 inner_text 直到满足算子（text 非 strict 语义，与 matches_assert
    一致：空白归一化后比）。供负文本断言（not_equals/not_contains）等需要在
    「首次满足后连续保持 stable 秒」的算子使用 —— Playwright 的
    not_to_have_text / not_to_contain_text 是立即负断言，表达不了稳定窗口。

    读取失败（元素缺失/轮询中途 detach）不是观测值：重试，超时带最后异常失败，
    绝不拿异常字符串参与 not_* 比较（否则会假通过）。
    """
    def err(last, last_error):
        if last_error is not None:
            return AssertionError(
                f"文本断言失败: 读取持续失败 op={op!r} expected={expected!r}: "
                f"{type(last_error).__name__}: {last_error}"
            )
        return AssertionError(
            f"文本断言失败: actual={last!r} expected={expected!r} op={op!r}"
        )
    return _poll_stable(
        lambda: locator.inner_text(),
        lambda v: matches_assert(v, expected, op),
        timeout=timeout, poll=poll, stable=stable, make_error=err,
    )


# ────────────────────────── 时间区间（前缀 + 时长） ──────────────────────────
#
# 与桌面侧 edr-wd 的 window_text_time_range 同一套语义（两边录制器要能
# 讲同一种断言语言）：
#
#   * HH:MM   → 整个一分钟 [HH:MM:00, HH:MM+1:00)
#   * HH:MM:SS + durationSeconds → [start, start+duration)
#   * 半开区间：起点算内，终点算外；跨午夜回绕。
#   * 从整段文本里扫所有时间记号（不是只看开头），所以「检查时间 10:20:59」
#     这种带前缀的内容也能命中 —— 与 timewindow 的「前缀匹配」不同，
#     这个比较的是时间值本身，不要求记号在文本开头。
#
# 选哪种：
#   * 要守「这条记录的时间在某个时间段里」，且起点精确到秒 → 时间区间；
#   * 只认「以某个字面前缀开头的显示值」 → 仍用 timewindow（波动分钟）。

_TIME_RANGE_TOKEN = re.compile(
    r"(?<![\d:+-])(?:[01]\d|2[0-3]):[0-5]\d(?::(?:[0-5]\d|\d))?(?![\d:])"
)


def _time_range_to_seconds(value: str) -> int:
    parts = tuple(int(p) for p in value.split(":"))
    return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) == 3 else 0)


def parse_time_range(prefix: str,
                     duration_seconds: int | None = None) -> tuple[int, int]:
    """返回 [start, start+duration) 的秒数。规则见上方注释。"""
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?",
                          str(prefix).strip())
    if match is None:
        raise ValueError(f"time_range_invalid_prefix: {prefix!r}")
    start = (_time_range_to_seconds(f"{match[1]}:{match[2]}:{match[3] or 0}")
             if match[3] is not None else
             int(match[1]) * 3600 + int(match[2]) * 60)
    if duration_seconds is None:
        duration_seconds = 1 if match[3] is not None else 60
    if (isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int)
            or not 1 <= duration_seconds <= 86400):
        raise ValueError(f"time_range_invalid_duration: {duration_seconds!r}")
    return start, duration_seconds


def matches_time_range(text: Any, prefix: str,
                      duration_seconds: int | None = None) -> list[str]:
    """text 里落在区间内的全部时间记号（可能多个，按出现顺序）。"""
    if text is None:
        return []
    start, duration = parse_time_range(prefix, duration_seconds)
    return [
        token
        for token in _TIME_RANGE_TOKEN.findall(str(text))
        if (_time_range_to_seconds(token) - start) % 86400 < duration
    ]


def expect_time_range(locator, prefix: str,
                      duration_seconds: int | None = None, *,
                      read: str = "text", timeout: float = 5.0) -> list[str]:
    """轮询元素文本，直到出现任一落在区间内的时间记号。

    read="value" 读输入框（时间填在 value 上，inner_text 恒为空）。
    返回命中的记号列表 —— 报错信息要打出「实际看到了什么」，
    只有「区间不匹配」这句话，人还得去页面上自己找当时的时间。
    """
    deadline = time.monotonic() + timeout
    last = None
    last_error = None
    while True:
        try:
            last = locator.input_value() if read == "value" else locator.inner_text()
            last_error = None
        except Exception as error:
            # 读取异常不进比较（错误不是观测值），预算内重试
            last_error = error
        if last_error is None:
            hits = matches_time_range(last, prefix, duration_seconds)
            if hits:
                return hits
        if time.monotonic() >= deadline:
            if last_error is not None:
                raise AssertionError(
                    f"时间区间断言失败: 读取持续失败 prefix={prefix!r} "
                    f"duration={duration_seconds!r}: {type(last_error).__name__}: {last_error}"
                )
            raise AssertionError(
                f"时间区间断言失败: prefix={prefix!r} "
                f"duration={duration_seconds!r} actual={last!r} "
                "（区间内没有时间记号）"
            )
        time.sleep(0.1)
