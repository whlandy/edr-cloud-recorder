"""`replay_trace --check`（云侧 P1/G1 回放就绪门槛）的回归测试。

G1 完成标准（recorder-replayability-design.md §5）要求一条命令能问「能不能回放」：
- 合法 v2 ready → 0，输出 READY / 可回放；
- v2 incomplete、契约损坏、旧 v1（无 $meta）→ 非 0，且给准确、可操作提示。

回归锚点：文档/SKILL 的调用形式是 `replay_trace.py --check <trace>`，旧代码没把
`--check` 注册成旗标，argparse 直接报 "unrecognized arguments: --check" 而不检查
任何轨迹 —— 这是本轮发现并修复的真实缺陷，第一条用例专门守它。
"""

import json

from replay_trace import _check_cli
from trace_v1 import to_v2


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _flat(status="ready"):
    """最省事的平铺 v1 式意图，经生产 trace_schema 组装成合法 v2。"""
    return {
        "schema": "edr.success-trace/v2",
        "name": "save-flow",
        "startUrl": "https://app.example/form",
        "status": status,
        "entry": "step-0001",
        "steps": {"step-0001": {"status": "ready", "next": None}},
    }


def test_check_cli_accepts_documented_double_dash_flag(tmp_path, capsys):
    # G1 文档/SKILL 的调用形式是 `--check <trace>`。旧代码没注册 --check，
    # argparse 直接 "unrecognized arguments: --check" 退出，根本到不了轨迹检查。
    p = _write(tmp_path, "ok.json", to_v2(_flat()))
    rc = _check_cli(["--check", p])
    out = capsys.readouterr().out
    assert rc == 0
    assert "READY" in out and "可回放" in out
    assert "unrecognized arguments" not in out


def test_check_cli_ready_v2_no_flag_backward_compat(tmp_path, capsys):
    # 不加 --check 的旧形式也必须继续可用。
    p = _write(tmp_path, "ok.json", to_v2(_flat()))
    assert _check_cli([p]) == 0
    assert "可回放" in capsys.readouterr().out


def test_check_cli_rejects_old_v1_with_actionable_hint(tmp_path, capsys):
    # 旧 v1（无 $meta）：非 0，且提示指向重录 / 转出，而不是白查生成器。
    v1 = {"schema": "edr.success-trace/v1", "name": "x",
          "entry": "step-0001", "steps": {}}
    p = _write(tmp_path, "v1.json", v1)
    rc = _check_cli(["--check", p])
    out = capsys.readouterr().out
    assert rc != 0
    assert "NOT REPLAYABLE" in out
    assert "旧版非 v2" in out and "record.py" in out


def test_check_cli_rejects_incomplete(tmp_path, capsys):
    p = _write(tmp_path, "inc.json", to_v2(_flat(status="incomplete")))
    rc = _check_cli(["--check", p])
    out = capsys.readouterr().out
    assert rc != 0
    assert "NOT READY" in out and "status != ready" in out


def test_check_cli_rejects_contract_damaged(tmp_path, capsys):
    # 契约损坏（不支持的动作）：非 0，指名是哪一步、什么动作。
    bad = to_v2(_flat())
    bad["step-0001"]["action"] = {"type": "Hover", "param": {}}
    p = _write(tmp_path, "bad.json", bad)
    rc = _check_cli(["--check", p])
    out = capsys.readouterr().out
    assert rc != 0
    assert "NOT REPLAYABLE" in out
    assert "不支持的动作" in out and "step-0001" in out
