"""P1（G1）验收：生成侧假绿路径关闭。

设计文档：references/assertion-hardening-design.md 阶段 1。
守的红线：生成器遇到无法**如实**转译的步骤（未知断言类型、不可转译
选择器、未知动作类型）时必须拒发整份 spec —— 断言不存在的用例跑绿
是最危险的一种假绿。修前这三条测试全部必挂（旧行为：静默跳过、
spec 照常写出）。
"""

import pytest

from generate_spec import SpecGenerationError, generate_spec

VALID = {"t": 100, "type": "assert", "assertion": "text", "expected": "管理员",
         "sel": 'getByText("管理员")'}


def test_unknown_assertion_type_rejects_generation():
    """修前：写一行注释后照常生成 spec（断言不存在，用例跑绿）。"""
    with pytest.raises(SpecGenerationError) as ei:
        generate_spec(
            [dict(VALID, assertion="html", expected="<b>x</b>")],
            [], start_url="https://example.test/", name="g1",
        )
    msg = str(ei.value)
    assert "未知断言类型" in msg and '"html"' in msg
    assert "step#0" in msg
    # 一次报全：问题列表可被机器消费
    assert ei.value.problems


def test_untranslatable_selector_rejects_generation():
    """修前：选择器注释掉、动作行照缺，spec 照常通过。"""
    with pytest.raises(SpecGenerationError) as ei:
        generate_spec(
            [{"t": 100, "type": "click", "kind": "text",
              "sel": 'getByFoo("Save")'}],
            [], start_url="https://example.test/", name="g2",
        )
    assert "无法转译选择器" in str(ei.value)
    assert "getByFoo" in str(ei.value)


def test_unknown_action_type_rejects_generation():
    """实现勘察补记的第三处：未知步骤类型整步被静默丢弃。"""
    with pytest.raises(SpecGenerationError) as ei:
        generate_spec(
            [{"t": 100, "type": "hover", "kind": "text",
              "sel": 'getByText("Menu")'}],
            [], start_url="https://example.test/", name="g3",
        )
    assert "未知动作类型" in str(ei.value)


def test_valid_recording_still_generates():
    """合法输入零行为变化：正常生成，内容完整，无拒发。

    既有语料回归（test_verify.py 对全部真实录制的参数化测试）守的是
    「输出逐字节不变」这一面；这里守「合法路径不被误伤」。
    """
    spec = generate_spec(
        [VALID], [], start_url="https://example.test/", name="g4")
    assert 'expect(' in spec and "管理员" in spec
    assert "未知断言类型" not in spec
    assert "无法转译" not in spec
