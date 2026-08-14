"""生成的 pytest 用例要用的三个小工具。

JS 版生成器靠 @playwright/test 的 expect 提供了三样东西，Python 版**都没有**：

    toMatchObject(...)     → 这里的 assert_subset
    expect.any(String)     → 这里的 ANY_STR
    expect.poll(fn).toBe() → 这里的 poll_until

所以生成的用例要 `from rec_assert import ...`。把它们放在一个文件里，
是为了让生成的用例只有一个额外依赖，拷到别的项目里也带得走。
"""

import time
from typing import Any


class _AnyOf:
    """占位符：只比类型，不比值。

    请求体里的 UUID、雪花 ID、时间戳每次运行都不一样，整条删掉又丢了
    「这个字段必须存在」的信息。用它换掉具体值，字段在不在、类型对不对
    仍然被守住。
    """

    __slots__ = ("_type", "_label")

    def __init__(self, type_: type, label: str):
        self._type = type_
        self._label = label

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, self._type)

    def __hash__(self):
        return hash(self._label)

    def __repr__(self) -> str:
        return self._label


ANY_STR = _AnyOf(str, "ANY_STR")
ANY_NUM = _AnyOf((int, float), "ANY_NUM")


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
