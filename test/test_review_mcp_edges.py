"""2026-10-23 评审轮新增：MCP replay_check 与 CLI --check 的契约边界。

覆盖：路径形态（目录/符号链接/越界/空字节）、损坏输入、协议字段语义、
连续调用状态隔离、只读保证、大体量输入的可用性。
不读取、不输出任何凭据；生产代码零改动。
"""

import json
import os
import time
from pathlib import Path

import pytest

from mcp_server import replay_check
from replay_trace import _check_cli
from trace_v1 import to_v2


def _ready_trace() -> dict:
    return to_v2({
        "entry": "step-0001",
        "steps": {"step-0001": {
            "status": "ready",
            "selector": {"kind": "css", "sel": 'locator("#a")'},
            "action": {"type": "Click", "param": {}},
        }},
    })


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _verdict(result_json: str) -> dict:
    return json.loads(result_json)


def _symlink_capable(tmp_path) -> bool:
    """探测本机能否创建符号链接（Windows 未开 Developer Mode 时不能）。

    不能时调用方 pytest.skip 点名原因；确定性反例由 mock 保留在
    test_windows_compat.py（PermissionError / 文件不存在 → 结构化失败）。
    """
    try:
        probe = tmp_path / "_symlink_probe_link"
        probe.symlink_to(tmp_path / "_symlink_probe_target")
        probe.unlink(missing_ok=True)
        return True
    except (OSError, NotImplementedError):
        return False


class TestReplayCheckPathForms:
    def test_directory_path_is_tool_error_not_ready(self, tmp_path):
        result = _verdict(replay_check(str(tmp_path)))
        assert result["ok"] is False
        assert "not a file" in result["error"]

    def test_broken_symlink_is_tool_error(self, tmp_path):
        if not _symlink_capable(tmp_path):
            pytest.skip("本机无符号链接能力（如 Windows 未开 Developer "
                        "Mode）；mock 反例见 test_windows_compat.py")
        link = tmp_path / "broken-link.json"
        link.symlink_to(tmp_path / "does-not-exist.json")
        result = _verdict(replay_check(str(link)))
        assert result["ok"] is False

    def test_valid_symlink_to_ready_trace_is_ready(self, tmp_path):
        if not _symlink_capable(tmp_path):
            pytest.skip("本机无符号链接能力（如 Windows 未开 Developer "
                        "Mode）；mock 反例见 test_windows_compat.py")
        real = Path(_write(tmp_path, "real.json", _ready_trace()))
        link = tmp_path / "link.json"
        link.symlink_to(real)
        result = _verdict(replay_check(str(link)))
        assert result["verdict"] == "READY"
        assert result["replayable"] is True

    def test_out_of_bounds_path_is_tool_error(self, tmp_path):
        result = _verdict(replay_check(str(tmp_path.parent.parent / "nope" / "x.json")))
        assert result["ok"] is False

    def test_empty_string_path_is_tool_error(self):
        result = _verdict(replay_check(""))
        assert result["ok"] is False

    def test_null_byte_path_does_not_claim_ready(self):
        # 现状允许抛 ValueError（fastmcp 会转成结构化 tool error）；
        # 无论返回还是抛错，都不得是 READY。
        try:
            result = _verdict(replay_check("bad\x00path.json"))
            assert result.get("verdict") != "READY"
        except ValueError:
            pass


class TestReplayCheckCorruptInputs:
    def test_non_utf8_bytes_not_replayable(self, tmp_path):
        p = tmp_path / "bin.json"
        p.write_bytes(b"\xff\xfe\x00binary")
        result = _verdict(replay_check(str(p)))
        assert result["verdict"] == "NOT REPLAYABLE"
        assert result["ok"] is True  # 工具调用成功，verdict 表达不可回放

    def test_empty_file_not_replayable(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        result = _verdict(replay_check(str(p)))
        assert result["verdict"] == "NOT REPLAYABLE"

    @pytest.mark.parametrize("raw,root_id", [
        ("[1,2,3]", "list"), ('"trace"', "str"), ("42", "int"),
        ("null", "null"), ("true", "bool"), ("3.14", "float")])
    def test_json_non_object_root_not_replayable(self, tmp_path, raw, root_id):
        # F2 修复（2026-10-23 第一批）：全部非对象根都是结构化
        # NOT REPLAYABLE 判定（ok:True + 可操作 reason），不是内部错误。
        p = tmp_path / f"root-{root_id}.json"
        p.write_text(raw, encoding="utf-8")
        result = _verdict(replay_check(str(p)))
        assert result["ok"] is True
        assert result["verdict"] == "NOT REPLAYABLE"
        assert result["node_count"] == 0

    def test_missing_status_is_not_ready(self, tmp_path):
        trace = _ready_trace()
        trace["$meta"]["attach"].pop("status")
        p = _write(tmp_path, "nostatus.json", trace)
        result = _verdict(replay_check(p))
        assert result["verdict"] == "NOT READY"

    def test_ready_trace_reports_node_count(self, tmp_path):
        p = _write(tmp_path, "ok.json", _ready_trace())
        result = _verdict(replay_check(p))
        assert result == {"ok": True, "path": p, "replayable": True,
                          "verdict": "READY", "reason": "", "node_count": 1}


class TestReplayCheckStabilityAndPurity:
    def test_consecutive_calls_stateless(self, tmp_path):
        # 交替查询两个不同文件 × 5 轮：verdict 必须零串扰
        ready = _write(tmp_path, "ready.json", _ready_trace())
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        for _ in range(5):
            assert _verdict(replay_check(ready))["verdict"] == "READY"
            assert _verdict(replay_check(str(bad)))["verdict"] == "NOT REPLAYABLE"

    def test_check_is_read_only(self, tmp_path, monkeypatch):
        # 只读保证：调用前后目录里不新增任何文件（不产生写动作）
        monkeypatch.chdir(tmp_path)
        ready = _write(tmp_path, "ready.json", _ready_trace())
        before = sorted(p.name for p in tmp_path.iterdir())
        for _ in range(3):
            replay_check(ready)
        after = sorted(p.name for p in tmp_path.iterdir())
        assert before == after == ["ready.json"]

    def test_large_trace_checked_quickly(self, tmp_path):
        # 2000 节点合法链：可用性上界（<5s），链必须走通不误报
        steps = {}
        for i in range(2000):
            steps[f"step-{i:04d}"] = {
                "status": "ready",
                "selector": {"kind": "css", "sel": f'locator("#el{i}")'},
                "action": {"type": "Click", "param": {}},
                "next": f"step-{i + 1:04d}" if i < 1999 else None,
            }
        trace = to_v2({"entry": "step-0000", "steps": steps})
        p = _write(tmp_path, "big.json", trace)
        start = time.monotonic()
        result = _verdict(replay_check(p))
        elapsed = time.monotonic() - start
        assert result["verdict"] == "READY"
        assert result["node_count"] == 2000
        assert elapsed < 5.0


class TestCheckCliContract:
    # F7 修复（2026-10-23 第二批）：目录输入不再裸栈崩溃，走 OSError 分支
    # 输出稳定诊断 + 非 0 退出（与 MCP 侧 not-a-file 语义对齐）。
    def test_cli_directory_path_fails_with_message(self, tmp_path, capsys):
        rc = _check_cli(["--check", str(tmp_path)])
        captured = capsys.readouterr()
        assert rc != 0
        assert "Traceback" not in captured.out + captured.err
        assert "IsADirectoryError" not in captured.out + captured.err
        assert str(tmp_path) in captured.out  # 诊断含路径

    @pytest.mark.parametrize("kind,fragment", [
        ("dir", "不是文件"),
        ("symlink", "文件不存在"),
        ("deep", "文件不存在"),
    ])
    def test_cli_unreadable_path_forms_fail_cleanly(
            self, tmp_path, capsys, kind, fragment):
        # 目录 / 断链符号链接 / 不存在的深层路径：rc!=0、无 traceback、
        # 诊断稳定（同一通道、含路径与 OS 错误类别）。
        if kind == "dir":
            target = tmp_path / "adir"
            target.mkdir()
        elif kind == "symlink":
            if not _symlink_capable(tmp_path):
                pytest.skip("本机无符号链接能力（如 Windows 未开 Developer "
                            "Mode）；mock 反例见 test_windows_compat.py")
            target = tmp_path / "broken-link.json"
            target.symlink_to(tmp_path / "no-such-target.json")
        else:
            target = tmp_path / "deep" / "missing" / "trace.json"
        rc = _check_cli(["--check", str(target)])
        captured = capsys.readouterr()
        assert rc != 0
        assert "Traceback" not in captured.out + captured.err
        for class_name in ("IsADirectoryError", "FileNotFoundError",
                           "PermissionError"):
            assert class_name not in captured.out + captured.err
        assert str(target) in captured.out
        assert fragment in captured.out

    def test_cli_unreadable_file_fails_cleanly(self, tmp_path, capsys):
        # chmod 0o000 的文件：无权限读取 → 结构化失败，不裸栈。
        if os.name == "nt":
            pytest.skip("Windows 的 chmod 只摆弄只读位，0o000『不可读』语义"
                        "不成立；ACL 拒读反例由 test_windows_compat.py 的 "
                        "mock 覆盖")
        p = tmp_path / "unreadable.json"
        p.write_text("{}", encoding="utf-8")
        os.chmod(p, 0o000)
        try:
            if os.access(p, os.R_OK):
                pytest.skip("当前身份（如 root）对 0o000 文件仍可读，"
                            "跳过不可读场景，不伪造失败")
            rc = _check_cli(["--check", str(p)])
            captured = capsys.readouterr()
            assert rc != 0
            assert "Traceback" not in captured.out + captured.err
            assert str(p) in captured.out
        finally:
            os.chmod(p, 0o644)

    def test_cli_error_path_stdout_purity(self, tmp_path, capsys):
        # 错误路径：stdout 不得出现 READY/可回放 字样；诊断走既有错误
        # 输出通道（与 TraceReplayError 分支同一条 NOT REPLAYABLE 通道）。
        rc = _check_cli(["--check", str(tmp_path)])
        captured = capsys.readouterr()
        assert rc != 0
        assert "READY" not in captured.out
        assert "可回放" not in captured.out
        assert captured.out.strip() != ""  # 诊断非空
        assert "Traceback" not in captured.out + captured.err

    def test_cli_directory_repeat_calls_stable(self, tmp_path, capsys):
        # 同一目录输入连调 3 次：rc 与 stdout/stderr 逐次一致。
        results = []
        for _ in range(3):
            rc = _check_cli(["--check", str(tmp_path)])
            captured = capsys.readouterr()
            results.append((rc, captured.out, captured.err))
        assert len(set(results)) == 1  # 完全一致（rc + 两路输出）
        assert results[0][0] != 0

    def test_cli_output_is_protocol_clean(self, tmp_path, capsys):
        # stdout 只有一行 verdict 摘要，没有调试噪音/空行污染
        p = _write(tmp_path, "ok.json", _ready_trace())
        assert _check_cli(["--check", p]) == 0
        out = capsys.readouterr()
        assert "READY" in out.out
        assert out.err == ""

    def test_cli_repeated_checks_same_verdict(self, tmp_path, capsys):
        p = _write(tmp_path, "ok.json", _ready_trace())
        first = _check_cli(["--check", p])
        capsys.readouterr()
        second = _check_cli(["--check", p])
        assert first == second == 0
