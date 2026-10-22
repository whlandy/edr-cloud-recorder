"""web-record MCP 工具 `replay_check` 的回归测试（G1）。

replay_check 与 `replay_trace --check` 同一判据（validate_trace + $meta ready），
是 eco 编排「能不能回放」门槛的黑盒出口：
- 合法 v2 ready → READY 且 replayable；
- v2 incomplete → NOT READY，点名缺模板/选择器；
- 旧 v1（无 $meta）/ 契约损坏 / 非法 JSON → NOT REPLAYABLE，给可操作去向；
- 文件不存在 → ok=False（工具本身不可用，不是「不可回放」的判定）。
"""

import json

import pytest

from mcp_server import replay_check
from trace_v1 import to_v2


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _flat(status="ready"):
    return {
        "schema": "edr.success-trace/v2",
        "name": "save-flow",
        "startUrl": "https://app.example/form",
        "status": status,
        "entry": "step-0001",
        "steps": {"step-0001": {"status": "ready", "next": None}},
    }


def test_replay_check_ready_v2(tmp_path):
    out = json.loads(replay_check(_write(tmp_path, "ok.json", to_v2(_flat()))))
    assert out["ok"] is True
    assert out["replayable"] is True
    assert out["verdict"] == "READY"
    assert out["node_count"] == 1


def test_replay_check_incomplete_v2(tmp_path):
    out = json.loads(replay_check(_write(tmp_path, "inc.json", to_v2(_flat("incomplete")))))
    assert out["ok"] is True
    assert out["replayable"] is False
    assert out["verdict"] == "NOT READY"
    assert "incomplete" in out["reason"]


def test_replay_check_old_v1_not_replayable(tmp_path):
    v1 = {"schema": "edr.success-trace/v1", "name": "x",
          "entry": "step-0001", "steps": {}}
    out = json.loads(replay_check(_write(tmp_path, "v1.json", v1)))
    assert out["replayable"] is False
    assert out["verdict"] == "NOT REPLAYABLE"
    assert "重录" in out["reason"] or "v2" in out["reason"]


def test_replay_check_contract_damaged_not_replayable(tmp_path):
    bad = to_v2(_flat())
    bad["step-0001"]["action"] = {"type": "Hover", "param": {}}
    out = json.loads(replay_check(_write(tmp_path, "bad.json", bad)))
    assert out["verdict"] == "NOT REPLAYABLE"
    assert "不支持的动作" in out["reason"]


def test_replay_check_malformed_json_not_replayable(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    out = json.loads(replay_check(str(p)))
    assert out["replayable"] is False
    assert out["verdict"] == "NOT REPLAYABLE"
    assert "JSON" in out["reason"]


def test_replay_check_missing_file_is_tool_error(tmp_path):
    out = json.loads(replay_check(str(tmp_path / "nope.json")))
    assert out["ok"] is False
    assert "error" in out
