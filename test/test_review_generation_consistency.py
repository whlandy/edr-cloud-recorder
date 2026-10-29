"""2026-10-23 评审轮新增：生成 → 真实执行一致性。

三个层次，都不只做字符串检查：
1. `_lit` 字面量序列化：任意形状的录制值必须落成**可编译**的 Python 字面量
   且 round-trip 相等（引号/反斜杠/换行/正则元字符/非 ASCII/None/0/False）；
2. 生成门 fail-closed：未知/非法算子必须拒发（G1 红线的算子面）——
   value/attribute/count 分支已与 text 分支对齐（2026-10-23 F5 修复；
   9f99671 时曾用 xfail(strict) 钉住与 text 分支的不一致）；
3. 真实 Chromium 两链对照：同一录制在生成链（exec 生成的 pytest 函数）与
   回放链（replay_trace）上对困难字面量判定一致。

2026-10-23 F5 修复改动 generate_spec.py：value/attribute/count 分支在
生成前校验 op（非空字符串且 ∈ VALUE_OPS/COUNT_OPS，否则进 problems
拒发整份 spec，与 text 分支同构）。评审报告：
REDACTED-LOCAL-PATH/ai-projects/doc/cloud-recorder-review-20261023.md。

2026-10-23 F9 修复再次改动 generate_spec.py 的文本扩展算子模板
（starts_with/ends_with/matches/is_blank/is_not_blank）：轮询闭包**局部**
捕获元素读取的 Playwright TimeoutError（元素缺失/读取期间 detach）返回
False 哨兵，让 poll_until 继续轮询、超时后与回放链同样以干净的
AssertionError 收场（§7.1 failure_category 对齐，原先生成链冒泡成
error 形态）；比较（matches_assert）留在 try 外，编程错误照常冒泡；
哨兵在比较前短路，is_not_blank 不拿读取失败当「非空」假通过。
修复前基线（--runxfail 实测）：四算子生成链全部
`failed: TimeoutError: Locator.inner_text: Timeout 1500ms exceeded.`。
原 `test_missing_element_extended_ops_fail_on_both_chains`（只钉两链都失败、
记录形态差异）已被本文件 TestMissingElementAssertionForm 完全覆盖并取代。
"""

import ast
import copy
import functools
import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError, sync_playwright, expect as pw_expect,
)

from chrome_path import resolve_chrome
from generate_spec import (
    SpecGenerationError, _lit, artifact_assert_consistency, generate_spec,
    pytest_assert_ids,
)
from generate_trace import generate_trace
from rec_assert import COUNT_OPS, VALUE_OPS, expect_value, poll_until
from replay_trace import replay_trace

URL = "https://gen-invalid.test/page"
# 困难字面量都埋在页面里：引号、&、尖括号、全角、emoji、正则元字符、反斜杠
# #empty 供 is_blank 正对照；#late 由页面加载 3200ms 后注入（先缺失后出现：
# 生成函数先花 ~2s 走 dismiss_overlays 探测，第一次读取随即以 TimeoutError
# 失败，之后才注入 —— 哨兵轮询必须能等到它。走路由注入是因为生成函数每次
# 都会重新 goto，evaluate 预约的定时器会被清掉）
HTML = """<!doctype html><meta charset="utf-8"><body>
  <div id="quoted">He said "hi" &amp; &lt;left&gt; ok</div>
  <div id="unicode">管理员 😀 全角ＡＢＣ</div>
  <div id="meta">a.b*c(d) [x] + $?^{|}</div>
  <input id="slash" value="a\\b'c">
  <input id="num" value="10">
  <div id="ml">a<br>b</div>
  <div id="empty"></div>
  <table id="rows"><tr><td>1</td></tr><tr><td>2</td></tr></table>
  <script>setTimeout(function(){var d=document.createElement('div');
    d.id='late';d.textContent='迟到文本';document.body.appendChild(d)},3200)</script>
</body>"""


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=resolve_chrome())
        context = browser.new_context(base_url=URL)
        pg = context.new_page()
        pg.route("**",
                 lambda route: route.fulfill(status=200, content_type="text/html",
                                             body=HTML))
        pg.set_default_timeout(1500)
        pw_expect.set_options(timeout=500)
        yield pg
        browser.close()


# ── 1. _lit 字面量序列化 ─────────────────────────────────────────────


TRICKY_VALUES = [
    'He said "hi"', "back\\slash", "line1\nline2", "tab\there",
    "正则元字符 .* [x] (y) {z} + ? ^ $ |",
    "管理员 😀 ＡＢＣ", "caf\u00e9", "cafe\u0301", "", " ", "  多  空白  ",
    None, 0, False, True, 1, -1, 1.5, -0.0, 10**18,
    ["a\"b", 'c\\d', "x", 0], {"k\"1": "v\\1", "空": "值"},
    "a" * 500, "\x01\x02control",
]


@pytest.mark.parametrize("value", TRICKY_VALUES, ids=repr)
def test_lit_produces_compilable_roundtrip_literal(value):
    literal = _lit(value)
    # 可编译：生成文件里的字面量必须是合法 Python
    compile(f"x = {literal}", "<lit>", "exec")
    # round-trip：AST 字面求值与原值相等（bool/int/None/str/list/dict）
    import ast as _ast
    assert _ast.literal_eval(literal) == value


def test_lit_none_at_top_level_is_python_none():
    assert _lit(None) == "None"


@pytest.mark.parametrize("value", [["a", None], {"k": None}],
                         ids=["list-with-none", "dict-with-none"])
def test_lit_nested_none_emits_json_null_not_python(value):
    """潜在风险钉住（当前 generate_spec 只对标量调用 _lit，故不可达）：
    _lit 依赖 json.dumps —— 容器内的 None 会落成 JSON `null`，
    那不是合法 Python 字面量（ast/compile 都拒收）。若未来把容器
    传进 _lit，生成的 spec 将直接 NameError。见报告风险节 R1。"""
    literal = _lit(value)
    with pytest.raises(ValueError):
        ast.literal_eval(literal)


def test_lit_lone_surrogate_rejected_at_compile():
    # 现状边界：孤立代理项经 ensure_ascii=False 原样进字面量，
    # compile 编码源码时即 UnicodeEncodeError（不是静默错误字面量）。
    # 录制器不产出这种值；列为风险不列为缺陷。见报告风险节 R1。
    literal = _lit("\ud800")
    with pytest.raises(UnicodeEncodeError):
        compile(f"x = {literal}", "<lit>", "exec")


# ── 2. 生成门 fail-closed：未知算子 ─────────────────────────────────


def _assert_step(assertion, expected, op=None, **extra):
    step = {"id": "assert-1", "t": 100, "type": "assert", "sel": 'locator("#x")',
            "assertion": assertion, "expected": expected}
    if op is not None:
        step["op"] = op
    step.update(extra)
    return step


# 合法算子面（单一来源 rec_assert）：value/attribute 用 VALUE_OPS，
# count 用 COUNT_OPS —— 每个合法算子一条生成正对照。
LEGAL_OP_CASES = (
    [("value", op, "x", {}) for op in VALUE_OPS]
    + [("attribute", op, "x", {"attribute": "href"}) for op in VALUE_OPS]
    + [("count", op, 1, {}) for op in COUNT_OPS]
)

# 坏算子面：未知 / 空串 / 非字符串（int、dict）/ 显式 None
BAD_OPS = ["regex_equals", "", 42, {"kind": "regex"}, None]


class _FakeLocator:
    def __init__(self):
        self.count_value = 0

    def input_value(self):
        return "x"

    def count(self):
        return self.count_value


class _FakePage:
    """生成的测试函数第一步是 page.goto(start_url) —— 用假页承接，
    只为验证断言 helper 的 fail-closed。"""

    def goto(self, url, **kwargs):
        pass

    def locator(self, selector, **kwargs):
        return _FakeLocator()

    def get_by_text(self, text, **kwargs):
        return _FakeLocator()


class TestUnknownOpFailsClosedAtGeneration:
    """G1 红线（references/assertion-hardening-design.md 阶段 1）：
    无法如实转译的步骤必须拒发整份 spec。value/attribute/count 分支
    （2026-10-23 F5 修复后）与 text 分支同构：op 必须是非空字符串且在
    rec_assert.VALUE_OPS / COUNT_OPS 内，否则进 problems、整份拒发；
    老录制「op 字段缺省」仍按 equals 放行（录制器从不写空串/null）。"""

    @pytest.mark.parametrize("assertion", ["value", "attribute", "count"],
                             ids=["value", "attribute", "count"])
    def test_unknown_op_rejects_whole_spec(self, assertion):
        expected = {"count": 1, "value": "x", "attribute": "x"}[assertion]
        with pytest.raises(SpecGenerationError) as ei:
            generate_spec([_assert_step(assertion, expected, op="regex_equals")],
                          [], start_url=URL, name="gate")
        assert "regex_equals" in str(ei.value)

    def test_text_unknown_op_still_rejected(self):
        # 对照组：text 分支的拒发行为（已修复面）必须保持
        with pytest.raises(SpecGenerationError):
            generate_spec([_assert_step("text", "x", op="regex_equals")],
                          [], start_url=URL, name="gate")

    def test_unknown_op_generated_spec_fails_closed_at_runtime(self):
        # 运行期兜底（第二道防线）。F5 修复后生成门先拒发（见上条），
        # 未知 op 的 spec 不再被产出 —— 这里直接钉住 rec_assert 侧不变
        # 的保证：任何绕过生成门的路径把未知 op 递给 helper，运行期
        # 仍必须炸出来而不是静默通过（matches_assert 的 ValueError）。
        with pytest.raises(ValueError, match="不支持的比较算子"):
            expect_value(_FakeLocator(), "x", op="regex_equals")

    def test_count_bool_expected_rejected_at_generation_runtime(self):
        # count 的 bool 期望在 helper 入口即拒（rec_assert.expect_count）
        spec = generate_spec([_assert_step("count", True)],
                             [], start_url=URL, name="cb")
        ns = {"__file__": str(Path(__file__).parent / "generated.py")}
        exec(compile(spec, "generated.py", "exec"), ns)
        test_fn = next(v for k, v in ns.items()
                       if k.startswith("test_") and callable(v))
        with pytest.raises(ValueError, match="非负整数"):
            test_fn(_FakePage())

    @pytest.mark.parametrize("assertion,op,expected,extra", LEGAL_OP_CASES,
                             ids=[f"{c[0]}:{c[1]}" for c in LEGAL_OP_CASES])
    def test_every_legal_op_generates_helper_and_compiles(
            self, assertion, op, expected, extra):
        """正对照（F5 修复）：**每个**合法算子都必须放行，落进对应的
        helper / Playwright 原生分支，且产物逐个 compile() 通过。"""
        spec = generate_spec(
            [_assert_step(assertion, expected, op=op, **extra)],
            [], start_url=URL, name="legal")
        if op == "equals" and assertion != "count":
            assert f"to_have_{assertion}(" in spec       # Playwright 原生分支
        else:
            assert {"value": "expect_value(",
                    "attribute": "expect_attribute(",
                    "count": "expect_count("}[assertion] in spec
            assert f"op={_lit(op)}" in spec
        compile(spec, "generated.py", "exec")            # 产物必须可编译

    @pytest.mark.parametrize("assertion", ["value", "attribute", "count"],
                             ids=["value", "attribute", "count"])
    @pytest.mark.parametrize("bad_op", BAD_OPS, ids=repr)
    def test_bad_op_rejects_whole_spec(self, assertion, bad_op):
        """反例：未知 / 空串 / 非字符串 / 显式 None 一律 SpecGenerationError，
        消息含该 op 的 repr。op 字段缺省不在此列（老录制兼容，见下条）。"""
        expected = {"count": 1, "value": "x", "attribute": "x"}[assertion]
        step = _assert_step(assertion, expected)
        step["op"] = bad_op                    # 显式写入（含 None 也要显式）
        with pytest.raises(SpecGenerationError) as ei:
            generate_spec([step], [], start_url=URL, name="gate")
        assert repr(bad_op) in str(ei.value)

    @pytest.mark.parametrize("assertion", ["value", "attribute", "count"],
                             ids=["value", "attribute", "count"])
    def test_absent_op_still_defaults_to_equals(self, assertion):
        # 兼容边界：老录制没有 op 字段，缺省 equals 必须继续放行
        # （recorder-inject 只在显式选了非 equals 时才落 op 字段）。
        expected = {"count": 1, "value": "x", "attribute": "x"}[assertion]
        extra = {"attribute": "href"} if assertion == "attribute" else {}
        spec = generate_spec([_assert_step(assertion, expected, **extra)],
                             [], start_url=URL, name="default")
        fragment = ("expect_count(" if assertion == "count"
                    else f"to_have_{assertion}(")
        assert fragment in spec
        compile(spec, "generated.py", "exec")


class TestExpectedUnconfirmedRejectsWholeSpec:
    """P0（common-web-assertion-design §5/§6）：需要 expected 的断言，expected
    未确认（expectedConfirmed=false）必须拒发整份 spec —— 系统不得把「当前看到
    什么」当成用户意图（§2 期望值由用户确认）。老录制缺字段视为已确认，保持兼容。"""

    @pytest.mark.parametrize("assertion", ["text", "value", "attribute", "count",
                                           "visible", "checked"],
                             ids=["text", "value", "attribute", "count",
                                  "visible", "checked"])
    def test_unconfirmed_expected_rejects(self, assertion):
        expected = {"text": "x", "value": "x", "attribute": "x", "count": 1,
                    "visible": True, "checked": True}[assertion]
        extra = {"attribute": "href"} if assertion == "attribute" else {}
        step = _assert_step(assertion, expected, **extra)
        step["expectedConfirmed"] = False          # 显式未确认
        with pytest.raises(SpecGenerationError) as ei:
            generate_spec([step], [], start_url=URL, name="unconf")
        assert "expectedConfirmed" in str(ei.value)

    @pytest.mark.parametrize("assertion", ["text", "count", "visible"],
                             ids=["text", "count", "visible"])
    def test_confirmed_expected_is_accepted(self, assertion):
        expected = {"text": "x", "count": 1, "visible": True}[assertion]
        step = _assert_step(assertion, expected)
        step["expectedConfirmed"] = True
        spec = generate_spec([step], [], start_url=URL, name="conf")
        compile(spec, "generated.py", "exec")      # 必须能生成

    def test_legacy_no_field_is_accepted(self):
        # 老录制没有 expectedConfirmed 字段 → 视为已确认，保持兼容
        spec = generate_spec([_assert_step("text", "x")],
                             [], start_url=URL, name="legacy")
        compile(spec, "generated.py", "exec")

    def test_is_blank_op_needs_no_confirmation(self):
        # is_blank / is_not_blank 不需要 expected，未确认字段不影响
        step = _assert_step("text", None, op="is_blank")
        step["expectedConfirmed"] = False
        spec = generate_spec([step], [], start_url=URL, name="blankop")
        compile(spec, "generated.py", "exec")

    def test_dynamic_expected_needs_no_confirmation(self):
        # expectedFrom（localtime/timewindow/timerange）的期望值由回放时钟决定，
        # 没有「预填自当前值」问题，未确认字段不拒发
        step = _assert_step("text", "12:00", expectedFrom={"kind": "timerange"})
        step["expectedConfirmed"] = False
        spec = generate_spec([step], [], start_url=URL, name="dyn")
        compile(spec, "generated.py", "exec")


class TestThreeArtifactAssertConsistency:
    """P0（§8）：recording / pytest / trace 三处断言的 id 与数量必须一一对应。
    故意删除或改写任一产物的断言，校验必须失败。"""

    @staticmethod
    def _artifacts():
        steps = [
            {**_assert_step("text", "x"), "id": "a1"},
            {**_assert_step("count", 2, op="at_least"), "id": "a2",
             "sel": 'locator("#rows tr")'},
        ]
        steps[0]["sel"] = 'locator("#x")'
        spec = generate_spec(steps, [], start_url=URL, name="cc")
        trace = generate_trace(steps, [], name="cc", start_url=URL)
        return steps, spec, trace

    def test_consistent_by_construction(self):
        steps, spec, trace = self._artifacts()
        assert artifact_assert_consistency(steps, spec, trace) == []
        # 两处断言数量都对得上
        assert len(pytest_assert_ids(spec)) == 2

    def test_delete_pytest_assertion_fails(self):
        steps, spec, trace = self._artifacts()
        tampered = re.sub(r" *# rec-assert: a2.*", "", spec, count=1, flags=re.S)
        problems = artifact_assert_consistency(steps, tampered, trace)
        assert problems and "pytest 断言" in problems[0]

    def test_rewrite_trace_assertion_id_fails(self):
        steps, spec, trace = self._artifacts()
        tr = copy.deepcopy(trace)
        target = next(k for k in tr if k != "$meta")  # 第一个节点
        tr[target]["attach"]["provenance"]["sourceStepId"] = "a9"
        problems = artifact_assert_consistency(steps, spec, tr)
        # 数量没变（还是 2 条）但 id 被换走 —— 决不能算「一致」
        assert problems and "trace 断言" in problems[0]

    def test_extra_recording_assertion_fails(self):
        steps, spec, trace = self._artifacts()
        jammed = [*steps, {**_assert_step("text", "y"), "id": "a3",
                           "sel": 'locator("#y")'}]
        problems = artifact_assert_consistency(jammed, spec, trace)
        assert problems and "pytest 断言" in problems[0]


# ── 2b. 生成调用的方法必须真实存在（AST 结构检查） ───────────────────

PLAYWRIGHT_TEXT_METHODS = {
    "to_have_text", "not_to_have_text", "to_contain_text",
    "not_to_contain_text", "to_be_visible", "to_be_hidden",
    "to_be_checked", "not_to_be_checked", "to_have_value",
    "to_have_attribute", "to_have_count",
}


def _generated_spec_exercising_all_assertions():
    steps = [
        _assert_step("text", "x", op="equals"),          # noqa: 下同
        _assert_step("text", "y", op="not_equals"),
        _assert_step("text", "z", op="contains"),
        _assert_step("text", "w", op="not_contains"),
        _assert_step("text", "pre", op="starts_with"),
        _assert_step("text", "suf", op="ends_with"),
        _assert_step("text", "a.*b", op="matches"),
        _assert_step("text", None, op="is_blank"),
        _assert_step("text", None, op="is_not_blank"),
        _assert_step("value", "5", op="greater_than"),
        _assert_step("attribute", "", attribute="href", op="is_not_blank"),
        _assert_step("count", 1, op="at_least"),
        _assert_step("count", 9, op="at_most"),
        _assert_step("visible", True),
        _assert_step("checked", True),
        _assert_step("text", "12:00", expectedFrom={"kind": "timerange"}),
        {"id": "s16", "t": 100, "type": "click", "kind": "text",
         "sel": 'getByText("下一步")'},
    ]
    return generate_spec(steps, [], start_url=URL, name="ast")


def test_generated_spec_calls_only_real_helpers_and_methods():
    """AST 层检查：测试体内每个被调用名要么是生成的 import/定义，
    要么是 Python 内建；expect(...) 上的属性方法必须属于
    Playwright 断言方法白名单（该白名单在 e2e 里全部真实执行过）。"""
    spec = _generated_spec_exercising_all_assertions()
    tree = ast.parse(spec)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported |= {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            imported |= {(alias.asname or alias.name).split(".")[0]
                         for alias in node.names}
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    # 内建名必须真实枚举（生成的测试体现在含 raise AssertionError(...) ——
    # F9 诊断包装）：__builtins__ 在被导入模块里是 dict（键即名字），
    # 在 __main__ 里是 module（dir 即名字）。旧写法 dir(dict) 得到字典方法、
    # 兜底清单又缺 AssertionError，会让结构检查误报「生成了不存在的调用」。
    builtins = (set(__builtins__) if isinstance(__builtins__, dict)
                else set(dir(__builtins__)))
    allowed = imported | defined | builtins
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name.startswith("test_"))
    called_names, expect_attrs = set(), set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.add(node.func.id)
        # expect(...) 链上的断言方法：Attribute 的 value 本身是对
        # 名字 expect 的调用
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "expect"):
            expect_attrs.add(node.attr)
    unknown = called_names - allowed
    assert unknown == set(), f"生成了不存在的调用: {unknown}"
    bad_methods = expect_attrs - PLAYWRIGHT_TEXT_METHODS
    assert bad_methods == set(), f"expect 上未知方法: {bad_methods}"
    # 白名单方法的真实存在性由本文件的 e2e 用例在真实 Chromium 里全部
    # 执行过（不能只查 dir() 名字）；这里守住“生成引用的名字都能解析”。
    # 生成的 import 必须可在本仓库解析（conftest 已加 sys.path）
    ns = {"__file__": str(Path(__file__).parent / "generated.py")}
    exec(compile(spec, "generated.py", "exec"), ns)  # NameError 即失败


# ── 3. 真实 Chromium 两链对照：困难字面量 ────────────────────────────


def _generated_outcome(pg, item):
    spec = generate_spec([item], [], start_url=URL, name="gen")
    code = compile(spec, "generated.py", "exec")
    ns = {"__file__": str(Path(__file__).parent / "generated.py")}
    exec(code, ns)
    test_fn = next(v for k, v in ns.items()
                   if k.startswith("test_") and callable(v))
    try:
        test_fn(pg)
        return "success"
    except Exception as error:
        # 失败形态（异常类型 + 消息）是本文件 F9 断言面的一部分，一并返回
        return f"failed: {type(error).__name__}: {error}"


def _replay_outcome(pg, item):
    trace = generate_trace([item], [], name="gen", start_url=URL)
    execution = replay_trace(pg, trace, navigate=False, timeout_ms=300)
    if execution["status"] == "success":
        return "success"
    step = execution["steps"][0] if execution["steps"] else {}
    error = step.get("error") or step.get("status")
    return f"failed: {type(error).__name__ if isinstance(error, BaseException) else error}"


TWO_CHAIN_CASES = [
    # (标签, 步骤, 两链都该 success 吗)
    ("引号&尖括号 equals", {**_assert_step("text", 'He said "hi" & <left> ok'),
                          "sel": 'locator("#quoted")'}, True),
    ("unicode equals", {**_assert_step("text", "管理员 😀 全角ＡＢＣ"),
                        "sel": 'locator("#unicode")'}, True),
    ("正则元字符 contains", {**_assert_step("text", "a.b*c(d)", op="contains"),
                        "sel": 'locator("#meta")'}, True),
    ("正则元字符 fullmatch", {**_assert_step(
        "text", "a\\.b\\*c\\(d\\) \\[x\\] \\+ \\$\\?\\^\\{\\|\\}", op="matches"),
        "sel": 'locator("#meta")'}, True),
    ("反斜杠引号 value 精确", {**_assert_step("value", "a\\b'c"),
                          "sel": 'locator("#slash")'}, True),
    ("count at_least", {**_assert_step("count", 2, op="at_least"),
                        "sel": 'locator("#rows tr")'}, True),
]


@pytest.mark.parametrize("label,item,should_pass", TWO_CHAIN_CASES,
                         ids=[c[0] for c in TWO_CHAIN_CASES])
def test_two_chains_agree_on_tricky_literals(page, label, item, should_pass):
    generated = _generated_outcome(page, item)
    replayed = _replay_outcome(page, item)
    if should_pass:
        assert generated == "success", f"生成链: {generated}"
        assert replayed == "success", f"回放链: {replayed}"
    else:
        assert generated.startswith("failed")
        assert replayed.startswith("failed")


def test_value_greater_than_two_chain_after_op_gate(page):
    """F5 修复对照：value greater_than（expect_value 轮询链）在生成门
    放行后，生成链与回放链都必须真实跑通 —— 算子校验没有把合法的
    扩展算子挡在门外。"""
    item = {**_assert_step("value", "9", op="greater_than"),
            "sel": 'locator("#num")'}
    assert _generated_outcome(page, item) == "success"
    assert _replay_outcome(page, item) == "success"


def test_multiline_equals_two_chain_behavior(page):
    """多行文本（<br> 渲染成 \\n）的 equals：回放链用 matches_assert
    （空白归一化后比），生成链用 Playwright to_have_text。
    记录现状判定，供报告引用。"""
    item = {**_assert_step("text", "a\nb"), "sel": 'locator("#ml")'}
    generated = _generated_outcome(page, item)
    replayed = _replay_outcome(page, item)
    # 两链必须**同判**（都成功或都失败）；判定不一致才是缺陷
    assert (generated == "success") == (replayed == "success"), (
        f"两链分歧: 生成链={generated!r} 回放链={replayed!r}")


# ── 4. F9：元素缺失 + 扩展算子的失败形态两链对齐 ─────────────────────

# 四类扩展文本算子（is_not_blank 的哨兵语义在无浏览器的假 locator 组单测）
EXTENDED_TEXT_OPS = ["starts_with", "ends_with", "matches", "is_blank"]


class TestMissingElementAssertionForm:
    """F9（评审报告 §3，2026-10-23 修复后转正；修复前为 xfail(strict)）：
    元素缺失 + 扩展文本算子时，生成链与回放链都必须以**干净的
    AssertionError**（assertion 形态，§7.1 failure_category=assertion）失败
    —— 不得让元素读取的 playwright TimeoutError 从 poll_until 冒泡成 error
    形态。修复前基线（--runxfail 实测，9f99671 + F5 工作树）：四算子生成链
    全部 `failed: TimeoutError: Locator.inner_text: Timeout 1500ms exceeded.`，
    回放链已是 AssertionError。这组断言由旧
    test_missing_element_extended_ops_fail_on_both_chains 的「两链都失败」
    强化而来（另加形态与诊断要求）。"""

    @pytest.mark.parametrize("op", EXTENDED_TEXT_OPS)
    def test_missing_element_fails_as_assertion_on_both_chains(self, page, op):
        expected = None if op == "is_blank" else "abc"
        item = {**_assert_step("text", expected, op=op),
                "sel": 'locator("#missing")'}
        generated = _generated_outcome(page, item)
        replayed = _replay_outcome(page, item)
        # 形态对齐：两链都是 AssertionError（不是 TimeoutError / error）
        assert generated.startswith("failed: AssertionError"), generated
        assert replayed.startswith("failed: AssertionError"), replayed
        # 诊断强度：生成链消息含元素定位与算子（供 §7.1 归因后人工排查）
        assert "#missing" in generated and op in generated, generated
        # 不得假通过（缺元素时回放结果只能是失败，不能是 success）
        assert replayed != "success" and generated != "success"


POSITIVE_EXTENDED_CASES = [
    ("starts_with", "He said", 'locator("#quoted")'),
    ("ends_with", "ok", 'locator("#quoted")'),
    ("matches", "He said.*", 'locator("#quoted")'),
    ("is_blank", None, 'locator("#empty")'),
]


@pytest.mark.parametrize("op,expected,sel", POSITIVE_EXTENDED_CASES,
                         ids=[c[0] for c in POSITIVE_EXTENDED_CASES])
def test_positive_extended_ops_pass_on_both_chains(page, op, expected, sel):
    """正对照：元素存在时，四类扩展算子在生成链与回放链都**通过** ——
    F9 的读取守卫只改「读取失败」路径，不得影响成功路径的判定。"""
    item = {**_assert_step("text", expected, op=op), "sel": sel}
    assert _generated_outcome(page, item) == "success"
    assert _replay_outcome(page, item) == "success"


def test_late_element_recovered_by_guarded_poll(page):
    """先缺失后出现：页面加载 3200ms 后 DOM 注入 #late。第一次读取以
    PlaywrightTimeoutError 失败、按哨兵继续轮询，预算内后续读取命中，
    生成链最终通过 —— 哨兵是「本轮未观测到」，不是终止信号。
    （修复前这个场景会直接以 TimeoutError 整体冒泡。）"""
    import time
    item = {**_assert_step("text", "迟到", op="starts_with"),
            "sel": 'locator("#late")'}
    started = time.monotonic()
    outcome = _generated_outcome(page, item)
    elapsed = time.monotonic() - started
    assert outcome == "success", outcome
    # 注入发生在首次读取失败之后：通过必然经历了「哨兵 → 继续轮询」，
    # 若首读即中（守卫失效、读取内联冒泡等回归），会在 ~2s 内返回
    assert elapsed >= 3.0, f"过早通过，未经历读取失败重试: {elapsed:.2f}s"


# ── 5. F9 守卫的边界：不吞编程错误、不假通过、结构可检查（无浏览器） ──


class _GuardProbeLocator:
    """inner_text 按注入的异常抛；count()=0 让 dismiss_overlays 立即返回。"""

    def __init__(self, read_error):
        self._read_error = read_error

    def inner_text(self, timeout=None):
        raise self._read_error

    def count(self):
        return 0


class _GuardProbePage:
    """假页：goto/dismiss_overlays 快速通过，断言定位器按脚本抛指定异常。"""

    def __init__(self, read_error):
        self._read_error = read_error

    def goto(self, url, **kwargs):
        pass

    def locator(self, selector, **kwargs):
        return _GuardProbeLocator(self._read_error)

    def get_by_text(self, text, **kwargs):
        return _GuardProbeLocator(self._read_error)


def _compiled_test_fn(spec):
    """exec 一份生成 spec，返回其中的测试函数（import 失败/语法坏即报错）。"""
    ns = {"__file__": str(Path(__file__).parent / "generated.py")}
    exec(compile(spec, "generated.py", "exec"), ns)
    return next(v for k, v in ns.items()
                if k.startswith("test_") and callable(v))


def test_missing_element_is_not_blank_sentinel_keeps_failing(monkeypatch):
    """is_not_blank 对读取失败哨兵必须继续失败：哨兵在比较**之前**短路
    （不会把 False/None 喂给 matches_assert 当「非空」观测值）——缺元素
    不得假通过，超时后走 AssertionError。monkeypatch 只把 poll 预算缩到
    0.6s 加速，poll_until 语义本身不动（同一函数、同一判定路径）。"""
    import rec_assert
    monkeypatch.setattr(
        rec_assert, "poll_until",
        functools.partial(rec_assert.poll_until, timeout=0.6))
    item = {**_assert_step("text", None, op="is_not_blank"),
            "sel": 'locator("#missing")'}
    fn = _compiled_test_fn(generate_spec([item], [], start_url=URL, name="nb"))
    with pytest.raises(AssertionError) as ei:
        fn(_GuardProbePage(PlaywrightTimeoutError("元素缺失")))
    assert "#missing" in str(ei.value), str(ei.value)
    assert "is_not_blank" in str(ei.value), str(ei.value)


def test_unexpected_read_error_propagates_not_swallowed():
    """不吞编程错误：闭包的 try 只包读取本身、只捕 PlaywrightTimeoutError；
    读取期间的意外异常（如注入的 ValueError）原样经 poll_until 冒泡，
    不被哨兵转换成 AssertionError。"""
    item = {**_assert_step("text", "abc", op="starts_with"),
            "sel": 'locator("#x")'}
    fn = _compiled_test_fn(generate_spec([item], [], start_url=URL, name="ve"))
    with pytest.raises(ValueError, match="boom: 编程错误"):
        fn(_GuardProbePage(ValueError("boom: 编程错误")))


def test_poll_until_still_propagates_fn_exceptions():
    """直接钉 rec_assert.poll_until 的既有传播语义（F9 的证据面）：
    fn 异常不进比较、不被吞。本修复只在生成闭包局部捕读取异常，
    rec_assert / poll_until 零改动。"""
    def bad():
        raise ValueError("fn 异常必须传播")

    with pytest.raises(ValueError, match="fn 异常必须传播"):
        poll_until(bad, True, timeout=0.2)


@pytest.mark.parametrize("op,expected", [
    ("starts_with", "pre"), ("ends_with", "suf"), ("matches", "a.*b"),
    ("is_blank", None), ("is_not_blank", None)],
    ids=["starts_with", "ends_with", "matches", "is_blank", "is_not_blank"])
def test_guarded_poll_body_structure_and_compile(op, expected):
    """含新 try/except 的生成体逐个 compile() + exec（import 可解析），
    并做结构检查：嵌套闭包内恰一个 Try、只捕 PlaywrightTimeoutError、
    except 体是哨兵 return False、matches_assert 比较在 Try 之外；
    外层轮询由 AssertionError 诊断包装（含定位/算子）并收闭包为 fn。"""
    spec = generate_spec([_assert_step("text", expected, op=op)],
                         [], start_url=URL, name="guard")
    compile(spec, "generated.py", "exec")
    _compiled_test_fn(spec)                          # import 层面真实可解析
    tree = ast.parse(spec)
    closures = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                and n.name.startswith("_text_assert_")]
    assert len(closures) == 1, "扩展算子应生成唯一的哨兵闭包"
    closure = closures[0]
    tries = [n for n in ast.walk(closure) if isinstance(n, ast.Try)]
    assert len(tries) == 1, "闭包内只应有一个 try（包读取本身）"
    handler = tries[0].handlers[0]
    assert (isinstance(handler.type, ast.Name)
            and handler.type.id == "PlaywrightTimeoutError")
    assert isinstance(handler.body[0], ast.Return)
    assert ast.literal_eval(handler.body[0].value) is False

    def calls_in(node):
        return {n.func.id for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

    # 比较逻辑在 try 外：Try（含 handler）里不得有 matches_assert，
    # 而整个闭包里有 —— 哨兵短路发生在比较之前
    assert "matches_assert" not in calls_in(tries[0])
    assert "matches_assert" in calls_in(closure)
    assert f".inner_text(timeout=1000)" in spec
    # 外层：poll_until 收闭包，except AssertionError 重新抛出并带诊断
    wrappers = [n for n in ast.walk(tree) if isinstance(n, ast.Try)
                and any(isinstance(h.type, ast.Name)
                        and h.type.id == "AssertionError"
                        for h in n.handlers)]
    assert len(wrappers) == 1
    poll_calls = [n for n in ast.walk(wrappers[0])
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "poll_until"]
    assert len(poll_calls) == 1
    assert poll_calls[0].args[0].id == closure.name
