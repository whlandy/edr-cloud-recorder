"""P3a（G3）验收：生成模板里业务与清理的结构隔离。

设计文档：references/assertion-hardening-design.md 阶段 3（方案 A：
契约先行 —— web 录制数据模型没有 cleanup 概念，隔离在模板层建立，
用户手写的清理动作落进隔离结构；replay 侧执行 cleanup 降级为设计
附录，等首个非空 cleanup 语料出现再实现）。
红线：清理失败绝不推翻业务结论（平台规范 §6.6）。
修前这些测试必挂：生成的 spec 没有 try/finally 隔离与 _run_cleanup 段。
"""

import re

from generate_spec import generate_spec

STEP = {"t": 100, "type": "assert", "assertion": "text", "expected": "Saved",
        "sel": 'getByText("Status")'}


def _spec():
    return generate_spec([STEP], [], start_url="https://example.test/",
                          name="cleanup-check")


def test_business_body_runs_inside_try_finally():
    spec = _spec()
    def_pos = spec.index("def test_cleanup_check(recorded_page: Page):")
    try_pos = spec.index("    try:", def_pos)
    finally_pos = spec.index("    finally:", try_pos)
    # 业务断言必须在 try 内（8 空格缩进）—— try 包住的是全部业务
    assert re.search(r"^        expect\(", spec[def_pos:finally_pos], re.M), spec
    # 清理调用在 finally 里：业务挂了它也跑
    assert "        _run_cleanup(page)" in spec[try_pos:finally_pos + 60], spec


def test_cleanup_section_catches_its_own_failures():
    spec = _spec()
    sec = spec[spec.index("def _run_cleanup(page):"):]
    # 清理异常被独立捕获 → 只 print 告警，不外抛、不改业务 PASS/FAIL
    assert "    try:" in sec and "except Exception as e:" in sec, sec
    assert "⚠ cleanup failed" in sec, sec
    # 占位段：数据源尚为空，契约先立（用户按 SKILL.md 流程手写清理）
    assert "TODO cleanup" in sec, sec


def test_generated_spec_still_compiles():
    """重缩进 + try 包裹后整份 spec 必须仍是合法 Python。"""
    compile(_spec(), "<spec>", "exec")
