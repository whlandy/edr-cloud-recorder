from __future__ import annotations

import json
import stat

from rec_session import restore_context_session, write_session_snapshot


class _Context:
    def __init__(self):
        self.calls = []

    def clear_cookies(self):
        self.calls.append(("clear_cookies",))

    def add_cookies(self, cookies):
        self.calls.append(("add_cookies", cookies))

    def add_init_script(self, *, script):
        self.calls.append(("add_init_script", script))


def _state():
    return {
        "cookies": [{
            "name": "sid", "value": "recorded-cookie",
            "domain": "app.example", "path": "/",
        }],
        "origins": [{
            "origin": "https://app.example",
            "localStorage": [{"name": "tenant", "value": "north"}],
        }],
    }


def test_recorded_session_restores_cookies_and_storage_before_navigation(tmp_path):
    auth_dir = tmp_path / ".auth"
    write_session_snapshot(
        auth_dir, _state(), {"token": "session-token"},
        session_origin="https://app.example",
    )
    context = _Context()

    assert restore_context_session(context, auth_dir) is True

    assert context.calls[0] == ("clear_cookies",)
    assert context.calls[1][0] == "add_cookies"
    assert context.calls[1][1][0]["value"] == "recorded-cookie"
    script = context.calls[2][1]
    assert "recorded-cookie" not in script
    assert "tenant" in script and "north" in script
    assert "session-token" in script
    assert "data.session.origin === location.origin" in script


def test_session_snapshot_is_owner_only_and_origin_scoped(tmp_path):
    auth_dir = tmp_path / ".auth"
    write_session_snapshot(
        auth_dir, _state(), {"token": "secret"},
        session_origin="https://app.example",
    )

    assert stat.S_IMODE(auth_dir.stat().st_mode) == 0o700
    for name in ("state.json", "session-storage.json"):
        assert stat.S_IMODE((auth_dir / name).stat().st_mode) == 0o600
    session = json.loads((auth_dir / "session-storage.json").read_text())
    assert session == {
        "origin": "https://app.example", "items": {"token": "secret"},
    }


def test_legacy_session_storage_format_remains_supported(tmp_path):
    auth_dir = tmp_path / ".auth"
    auth_dir.mkdir()
    (auth_dir / "state.json").write_text(json.dumps(_state()))
    (auth_dir / "session-storage.json").write_text(json.dumps({"legacy": "yes"}))
    context = _Context()

    assert restore_context_session(context, auth_dir) is True
    assert "legacy" in context.calls[-1][1]


def test_new_snapshot_removes_stale_session_storage(tmp_path):
    auth_dir = tmp_path / ".auth"
    write_session_snapshot(auth_dir, _state(), {"old": "token"})
    assert (auth_dir / "session-storage.json").exists()

    write_session_snapshot(auth_dir, _state(), None)

    assert not (auth_dir / "session-storage.json").exists()
