"""生成的 pytest 用例要用的三个小工具。

JS 版生成器靠 @playwright/test 的 expect 提供了三样东西，Python 版**都没有**：

    toMatchObject(...)     → 这里的 assert_subset
    expect.any(String)     → 这里的 ANY_STR
    expect.poll(fn).toBe() → 这里的 poll_until

所以生成的用例要 `from rec_assert import ...`。把它们放在一个文件里，
是为了让生成的用例只有一个额外依赖，拷到别的项目里也带得走。
"""

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
            problems.append(f"{path}: 期望 {expected}，实际 {actual!r}")
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
    while True:
        try:
            actual = locator.input_value() if read == "value" else locator.inner_text()
        except Exception as error:
            actual = f"{type(error).__name__}: {error}"
        else:
            if matches_local_time(actual, fmt, match=match, slack_seconds=slack_seconds):
                return actual
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"时间断言失败: actual={actual!r} "
                f"期望{'含有' if match == 'contains' else '等于'}此刻时间 "
                f"{local_time_candidates(fmt, slack_seconds)}（fmt={fmt!r}）"
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
