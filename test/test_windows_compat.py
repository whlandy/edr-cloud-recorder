"""Windows 适配批次 A 回归。

只覆盖代码级兼容（W1–W4），不做真实 Windows 冒烟（批次 B）。
macOS/Linux 上通过注入 platform 标志与假 os 调用模拟 Windows 行为，
真实 Windows 执行证据仍待批次 B。

TDD 约定：先写失败回归，再最小修复（W1/W2/W3 的用例在修复前应为 RED）。
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "assets"))
sys.path.insert(0, str(ROOT / "scripts"))

import rec_config  # noqa: E402
import rec_session  # noqa: E402
from mcp_server import replay_check  # noqa: E402  # 必须在 monkeypatch Path 之前导入
from replay_trace import _check_cli  # noqa: E402


# ── W2：默认用户配置目录（Windows APPDATA → LOCALAPPDATA → 用户目录；POSIX XDG 不变） ──


class TestUserConfigPath:
    def test_win32_prefers_appdata(self):
        p = rec_config.user_config_path(
            {"APPDATA": r"C:\Users\u\AppData\Roaming"}, platform="nt")
        assert str(p) == os.path.join(
            r"C:\Users\u\AppData\Roaming", "edr-cloud-recorder", "config.json")

    def test_win32_falls_back_to_localappdata(self):
        p = rec_config.user_config_path(
            {"LOCALAPPDATA": r"C:\Users\u\AppData\Local"}, platform="nt")
        assert str(p) == os.path.join(
            r"C:\Users\u\AppData\Local", "edr-cloud-recorder", "config.json")

    def test_win32_falls_back_to_home_dir(self):
        p = rec_config.user_config_path({}, platform="nt")
        assert p == Path.home() / "edr-cloud-recorder" / "config.json"

    def test_posix_uses_xdg_and_ignores_appdata(self):
        # POSIX 不因 APPDATA 存在而回退（Windows 变量在 POSIX 上无效）
        p = rec_config.user_config_path(
            {"XDG_CONFIG_HOME": "/xdg", "APPDATA": r"C:\Roaming"},
            platform="posix")
        assert str(p) == os.path.join(
            "/xdg", "edr-cloud-recorder", "config.json")

    def test_posix_without_xdg_uses_home_dot_config(self):
        p = rec_config.user_config_path({}, platform="posix")
        assert p == Path.home() / ".config" / "edr-cloud-recorder" / "config.json"

    def test_module_constant_matches_runtime_platform(self):
        # 导入期常量与运行平台一致（现有消费方 USER_CONFIG 语义不变）
        expected = rec_config.user_config_path(platform=os.name)
        assert rec_config.USER_CONFIG == expected

    def test_explicit_config_still_wins(self, tmp_path):
        # 显式 --config 优先级不变（回归）
        cfg = tmp_path / "mine.json"
        cfg.write_text(json.dumps({"auth": {"user": "u"}}), encoding="utf-8")
        loaded = rec_config.load_config(str(cfg))
        assert loaded["auth"]["user"] == "u"
        assert loaded["_path"] == str(cfg)


# ── W2：密码权限提示（Windows 无 chmod 建议、不泄密；POSIX 行为不变） ──


def _config_with_password(tmp_path: Path, password: str = "S3cret-P@ss") -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"auth": {"user": "u", "password": password}}),
                 encoding="utf-8")
    return p


class TestPasswordHint:
    def test_win32_hint_has_no_chmod_and_no_secret(self, tmp_path, capsys,
                                                  monkeypatch):
        p = _config_with_password(tmp_path)
        monkeypatch.setattr(rec_config, "_IS_WINDOWS", True)
        rec_config.load_config(str(p))
        out = capsys.readouterr().out
        assert "chmod 600" not in out            # 不给 POSIX chmod 建议
        assert "S3cret-P@ss" not in out          # 不泄密
        assert str(p) in out                      # 提到文件位置
        assert ("ACL" in out) or ("配置目录" in out)  # Windows 语义文案

    def test_win32_wide_mode_does_not_trigger_posix_check(self, tmp_path,
                                                          capsys, monkeypatch):
        # Windows 下不做 POSIX mode-bit 检查（Windows 的 st_mode 不是 ACL）
        p = _config_with_password(tmp_path)
        monkeypatch.setattr(rec_config, "_IS_WINDOWS", True)
        rec_config.load_config(str(p))
        assert capsys.readouterr().out.count("\n") == 1  # 恰一条 ACL 提示

    def test_posix_keeps_chmod_600_hint(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(rec_config, "_IS_WINDOWS", False)
        p = _config_with_password(tmp_path)
        os.chmod(p, 0o644)  # 权限过宽
        try:
            rec_config.load_config(str(p))
            out = capsys.readouterr().out
            assert "chmod 600" in out
            assert "S3cret-P@ss" not in out  # POSIX 同样不泄密
        finally:
            os.chmod(p, 0o600)

    def test_posix_tight_mode_no_hint(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(rec_config, "_IS_WINDOWS", False)
        p = _config_with_password(tmp_path)
        os.chmod(p, 0o600)
        rec_config.load_config(str(p))
        assert "chmod" not in capsys.readouterr().out

    def test_no_password_no_hint(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(rec_config, "_IS_WINDOWS", True)
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"auth": {"user": "u"}}), encoding="utf-8")
        rec_config.load_config(str(p))
        assert capsys.readouterr().out == ""


# ── W1：会话写盘（无 fchmod 仍原子；异常不留临时文件） ──


def _leftover_temps(directory: Path) -> list[str]:
    return [f.name for f in directory.iterdir() if f.name.startswith(".")]


class TestSessionWrite:
    def test_win32_without_fchmod_writes_successfully(self, tmp_path,
                                                     monkeypatch):
        # 模拟 Windows：非 POSIX 分支 + fchmod 不存在
        monkeypatch.setattr(rec_session, "POSIX", False)
        monkeypatch.setattr(os, "fchmod",
                            lambda *a, **k: pytest.fail(
                                "Windows 分支不得调用 os.fchmod"))
        target = tmp_path / "sessions" / "session.json"
        rec_session._write_private_json(target, {"items": {}})
        assert json.loads(target.read_text(encoding="utf-8")) == {"items": {}}
        assert _leftover_temps(target.parent) == []

    def test_win32_does_not_chmod_mode_bits(self, tmp_path, monkeypatch):
        # Windows 不做 mode-bit chmod（mode bits 不等价于 ACL，不得伪装）
        calls: list[tuple] = []
        monkeypatch.setattr(rec_session, "POSIX", False)
        monkeypatch.setattr(os, "fchmod",
                            lambda *a, **k: pytest.fail("不得 fchmod"))
        monkeypatch.setattr(Path, "chmod",
                            lambda self, mode: calls.append((self, mode)))
        target = tmp_path / "session.json"
        rec_session._write_private_json(target, {})
        assert calls == []  # 路径 chmod 一律不发生

    def test_posix_keeps_private_mode_bits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rec_session, "POSIX", os.name == "posix")
        if os.name != "posix":
            pytest.skip("POSIX mode bits 仅在 POSIX 上可断言")
        target = tmp_path / "session.json"
        rec_session._write_private_json(target, {})
        import stat
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) & 0o077 == 0

    def test_write_exception_leaves_no_temp_file(self, tmp_path, monkeypatch):
        # fsync 中途失败：异常冒泡、目标未产出、同目录无遗留临时文件
        # （Windows 上临时文件被占用时 unlink 会失败——因此失败前必须先关 fd）
        def boom(_fileno):
            raise OSError("simulated fsync failure")

        monkeypatch.setattr(os, "fsync", boom)
        target = tmp_path / "session.json"
        with pytest.raises(OSError, match="simulated fsync failure"):
            rec_session._write_private_json(target, {"k": "v"})
        assert not target.exists()
        assert _leftover_temps(tmp_path) == []

    def test_replace_failure_leaves_no_temp_file(self, tmp_path, monkeypatch):
        real_replace = os.replace

        def fail_replace(src, dst, **kwargs):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(os, "replace", fail_replace)
        target = tmp_path / "session.json"
        with pytest.raises(OSError, match="simulated replace failure"):
            rec_session._write_private_json(target, {"k": "v"})
        assert not target.exists()
        assert _leftover_temps(tmp_path) == []
        assert real_replace  # 占位避免 lint

    def test_atomic_replace_uses_same_dir_temp(self, tmp_path, monkeypatch):
        # 原子替换契约：临时文件与目标同目录、前缀 .目标名.，经 os.replace 落位
        seen: dict[str, Path] = {}
        real_replace = os.replace

        def spy_replace(src, dst, **kwargs):
            seen["src"], seen["dst"] = Path(src), Path(dst)
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)
        target = tmp_path / "session.json"
        rec_session._write_private_json(target, {"ok": True})
        assert seen["dst"] == target
        assert seen["src"].parent == target.parent
        assert seen["src"].name.startswith(f".{target.name}.")
        assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


# ── W3：check_visual 不依赖 /dev/null ──


class TestCheckVisualPortability:
    def test_source_has_no_dev_null_dependency(self):
        text = (ROOT / "scripts" / "check_visual.py").read_text(
            encoding="utf-8")
        assert "/dev/null" not in text

    def test_untracked_whitespace_check_runs_on_this_repo(self):
        # 功能级：当前仓库（含本未跟踪文件）跑 untracked whitespace 检查
        # 不因缺 /dev/null 或设备路径差异失败；语义保持 rc∈{0,1} 不抛错。
        import check_visual
        check_visual.check_untracked_whitespace()


# ── W4 的确定性反例（mock，不依赖真实 symlink/chmod 能力） ──


class TestStructuredFailuresWithoutOsCapabilities:
    """symlink/ACL 能力不足的平台用 mock 保留结构化失败反例：
    broken symlink ≈ FileNotFoundError、不可读 ≈ PermissionError。
    （真实 symlink/chmod 集成用例在 test_review_mcp_edges.py 里点名 skip。）
    """

    def test_replay_check_read_permission_error_is_not_replayable(
            self, tmp_path, monkeypatch):
        # ACL 拒绝读取（Windows 现实场景）：结构化 NOT REPLAYABLE，非内部崩溃
        p = tmp_path / "trace.json"
        p.write_text("{}", encoding="utf-8")

        def deny(self, *a, **k):
            raise PermissionError(13, "Permission denied", str(self))

        monkeypatch.setattr(Path, "read_text", deny)
        result = json.loads(replay_check(str(p)))
        assert result["ok"] is True
        assert result["verdict"] == "NOT REPLAYABLE"
        assert "Permission" in result["reason"] or "读不出" in result["reason"]

    def test_replay_check_missing_file_is_not_replayable(self, tmp_path):
        # 坏链接 ≈ 文件不存在：not-a-file 结构化错误（ok:False），非内部崩溃
        result = json.loads(replay_check(str(tmp_path / "broken-link.json")))
        assert result["ok"] is False
        assert "not a file" in result["error"]

    def test_cli_permission_error_structured_no_traceback(
            self, tmp_path, capsys, monkeypatch):
        p = tmp_path / "trace.json"
        p.write_text("{}", encoding="utf-8")

        def deny(self, *a, **k):
            raise PermissionError(13, "Permission denied", str(self))

        monkeypatch.setattr(Path, "read_text", deny)
        rc = _check_cli(["--check", str(p)])
        captured = capsys.readouterr()
        assert rc != 0
        assert "Traceback" not in captured.out + captured.err
        assert "PermissionError" not in captured.out + captured.err
        assert str(p) in captured.out
