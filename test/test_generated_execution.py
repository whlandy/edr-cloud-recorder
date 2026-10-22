"""同一录制在两条执行链上的判定对照（验收 2026-10-21 的核心要求）。

- 生成链：generate_spec 出的 pytest 函数**在真实 Chromium 里执行**
  （不是只看渲染字符串，也不是只看 compile 成功）；
- 回放链：generate_trace 出的轨迹走 replay_trace。

每条用例两条链的通过/失败判定必须一致，且等于该断言的本意。
覆盖：正例、反例、读异常（缺元素）、非法配置（两条链都快速失败）。
"""

import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect as pw_expect

from chrome_path import resolve_chrome
from generate_spec import SpecGenerationError, generate_spec
from generate_trace import generate_trace
from replay_trace import replay_trace

URL = "https://gen.invalid/page"
HTML = """<!doctype html><meta charset="utf-8"><body>
  <div id="msg">prefix abc suffix</div>
  <div id="multi">a  b  c</div>
  <input id="value" value="a  b">
  <input id="num" value="10">
  <input id="vtime" value="12:00">
  <div id="time">操作时间 2026-10-21 12:00:30</div>
  <table id="rows"><tr><td>1</td></tr><tr><td>2</td></tr><tr><td>3</td></tr></table>
  <div id="gone" style="display:none">gone</div>
  <a id="noattr">no attr</a>
  <a id="emptyattr" href="">empty attr</a>
  <div id="blank">   </div>
  <div id="line">line1
line2</div>
  <div id="hid">shown<span style="display:none">hidden</span></div>
  <div id="dynbox">base<span id="dyn" style="display:none"> dyn</span></div>
  <script>setTimeout(function(){var d=document.getElementById('dyn');
    if(d){d.style.display='';}},150);</script>
</body>"""


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=resolve_chrome())
        # 生成链第一步是 page.goto(录制起始路径，相对) —— 用 base_url 解析、
        # 用 route 承接，让导航落在本页 HTML 上（与真实首启一致）。
        context = browser.new_context(base_url=URL)
        pg = context.new_page()
        pg.route("**",
                lambda route: route.fulfill(status=200, content_type="text/html", body=HTML))
        pg.set_default_timeout(1500)
        pw_expect.set_options(timeout=500)
        yield pg
        browser.close()


def _item(assertion, expected, selector, **extra):
    return {
        "id": "assert-1", "t": 100, "type": "assert",
        "sel": f'locator("{selector}")',
        "assertion": assertion, "expected": expected, **extra,
    }


def _generated_outcome(pg, item):
    """执行生成链：spec 必须可编译，测试函数必须真实跑完。"""
    spec = generate_spec([item], [], start_url=URL, name="gen")
    code = compile(spec, "generated.py", "exec")     # D5：缺省时长不得生成坏语法
    ns = {"__file__": str(Path(__file__))}
    exec(code, ns)
    test = next(v for k, v in ns.items()
                if k.startswith("test_") and callable(v))
    try:
        test(pg)
        return "success"
    except Exception as error:
        return f"failed: {type(error).__name__}: {str(error).splitlines()[0]}"


def _replay_outcome(pg, item):
    trace = generate_trace([item], [], name="gen", start_url=URL)
    execution = replay_trace(pg, trace, navigate=False, timeout_ms=300)
    if execution["status"] == "success":
        return "success"
    step = execution["steps"][0] if execution["steps"] else {}
    return f"failed: {step.get('error') or step.get('status')}"


CASES = [
    # (标签, 步骤, 两条链都该 success 吗)
    ("contains 正例", _item("text", "abc", "#msg", op="contains"), True),
    ("contains 反例", _item("text", "xyz", "#msg", op="contains"), False),
    ("not_contains 正例", _item("text", "xyz", "#msg", op="not_contains"), True),
    ("not_contains 反例", _item("text", "abc", "#msg", op="not_contains"), False),
    # D3：正则全串 —— 裸 "abc" 不能放过 "prefix abc suffix"
    ("matches 全串反例(D3)", _item("text", "abc", "#msg", op="matches"), False),
    ("matches 全串正例", _item("text", "prefix abc suffix", "#msg", op="matches"), True),
    ("matches 分组正例", _item("text", "pre(fix) abc suf(fix)", "#msg", op="matches"), True),
    ("matches 分组反例", _item("text", "pre(fix)X", "#msg", op="matches"), False),
    ("matches 尾锚正例", _item("text", "a b c$", "#multi", op="matches"), True),
    ("matches 尾锚反例", _item("text", "a b c d$", "#multi", op="matches"), False),
    ("matches 换行归一正例", _item("text", "line1 line2", "#line", op="matches"), True),
    ("matches 原始换行反例", _item("text", "line1\nline2", "#line", op="matches"), False),
    ("starts_with 正例", _item("text", "prefix", "#msg", op="starts_with"), True),
    ("starts_with 反例", _item("text", "abc", "#msg", op="starts_with"), False),
    ("ends_with 正例", _item("text", "suffix", "#msg", op="ends_with"), True),
    ("ends_with 反例", _item("text", "prefix", "#msg", op="ends_with"), False),
    # D4：value/attribute 精确语义 —— 空白不归一、缺失属性 != 空属性
    ("value equals 反例(D4空白)", _item("value", "a b", "#value"), False),
    ("value equals 正例(精确)", _item("value", "a  b", "#value"), True),
    ("value not_equals 正例", _item("value", "a b", "#value", op="not_equals"), True),
    ("value 数值大于正例", _item("value", "9", "#num", op="greater_than"), True),
    ("value 数值大于反例(非数值)", _item("value", "9", "#value", op="greater_than"), False),
    ("attribute equals 正例(空属性)", _item("attribute", "", "#emptyattr", attribute="href"), True),
    ("attribute equals 反例(D4缺失属性)", _item("attribute", "", "#noattr", attribute="href"), False),
    ("attribute not_equals 正例(缺失)", _item("attribute", "", "#noattr", attribute="href", op="not_equals"), True),
    ("count at_least 正例", _item("count", 2, "#rows tr", op="at_least"), True),
    ("count equals 反例", _item("count", 9, "#rows tr"), False),
    ("count 缺元素(读成功, 0 是合法观测)", _item("count", 0, "#missing tr", op="at_most"), True),
    ("timerange 秒级正例", _item("text", "12:00:30", "#time",
                                expectedFrom={"kind": "timerange", "durationSeconds": 1}), True),
    ("timerange 秒级反例(终点外)", _item("text", "12:00:31", "#time",
                                    expectedFrom={"kind": "timerange", "durationSeconds": 1}), False),
    ("timerange 分钟缺省正例", _item("text", "12:00", "#time",
                                expectedFrom={"kind": "timerange"}), True),
    ("timerange value 缺省(D5)", _item("value", "12:00", "#vtime",
                                 expectedFrom={"kind": "timerange"}), True),
    ("读异常: 缺元素 text 反例", _item("text", "abc", "#missing"), False),
    ("缺元素可见性=否 正例", _item("visible", False, "#missing"), True),
    # D6：隐藏后代不在文本语义里 —— 四条原生文本分支必须与 Trace 同读 inner_text
    ("D6 contains 隐藏后代 反例", _item("text", "hidden", "#hid", op="contains"), False),
    ("D6 equals 可见部分 正例", _item("text", "shown", "#hid"), True),
    ("D6 equals 含隐藏文本 反例", _item("text", "shownhidden", "#hid"), False),
    ("D6 not_contains 隐藏 正例", _item("text", "hidden", "#hid", op="not_contains"), True),
    ("D6 not_equals 含隐藏 正例", _item("text", "shownhidden", "#hid", op="not_equals"), True),
    ("动态显隐 contains 正例", _item("text", "dyn", "#dynbox", op="contains"), True),
    ("动态显隐 equals 正例", _item("text", "base dyn", "#dynbox"), True),
]


@pytest.mark.parametrize("label,item,should_pass", CASES, ids=[c[0] for c in CASES])
def test_two_paths_agree(page, label, item, should_pass):
    gen = _generated_outcome(page, item)
    rep = _replay_outcome(page, item)
    gen_pass = gen == "success"
    rep_pass = rep == "success"
    assert gen_pass == should_pass, f"{label}: 生成链 {gen!r}（期望 {'success' if should_pass else 'failed'}）"
    assert rep_pass == should_pass, f"{label}: 回放链 {rep!r}（期望 {'success' if should_pass else 'failed'}）"
    assert gen_pass == rep_pass, f"{label}: 两条链不一致 生成={gen!r} 回放={rep!r}"


def test_d2_generated_contains_runs_in_real_browser(page):
    # 验收反例：to_contain 不存在 → AttributeError。必须真实执行，不能只看字符串。
    outcome = _generated_outcome(page, _item("text", "abc", "#msg", op="contains"))
    assert outcome == "success", outcome
    outcome = _generated_outcome(page, _item("text", "xyz", "#msg", op="not_contains"))
    assert outcome == "success", outcome


def test_invalid_config_fails_fast_on_both_paths(page):
    item = _item("text", "x", "#msg", op="regex-ish")
    # 生成链：拒发（不产出缺断言的 spec）
    with pytest.raises(SpecGenerationError):
        generate_spec([item], [], start_url=URL, name="gen")
    # 回放链：快速失败并指明不支持的算子
    trace = generate_trace([item], [], name="gen", start_url=URL)
    execution = replay_trace(page, trace, navigate=False, timeout_ms=300)
    assert execution["status"] == "failed"
    assert "不支持的比较算子" in json.dumps(execution, ensure_ascii=False)


def test_d5_default_duration_compiles_runs_and_rejects_zero(page):
    # 缺省时长：必须编译通过且真实执行通过（value 12:00 → 整分钟命中）
    item = _item("value", "12:00", "#vtime", expectedFrom={"kind": "timerange"})
    spec = generate_spec([item], [], start_url=URL, name="gen")
    assert "expect_time_range(" in spec
    compile(spec, "d5_default.py", "exec")
    ns = {"__file__": str(Path(__file__))}
    exec(compile(spec, "d5_default.py", "exec"), ns)
    test = next(v for k, v in ns.items() if k.startswith("test_") and callable(v))
    test(page)

    # 非法时长 0：两条链都不得静默当缺省
    bad = _item("value", "12:00", "#vtime",
                expectedFrom={"kind": "timerange", "durationSeconds": 0})
    with pytest.raises(SpecGenerationError):
        generate_spec([bad], [], start_url=URL, name="gen")
    trace = generate_trace([bad], [], name="gen", start_url=URL)
    execution = replay_trace(page, trace, navigate=False, timeout_ms=300)
    assert execution["status"] == "failed"
    assert "时长非法" in json.dumps(execution, ensure_ascii=False)
