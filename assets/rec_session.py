"""Secure persistence and restoration of a recorded browser session."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


def _write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        temporary_path.unlink(missing_ok=True)


def _session_payload(raw: Any, origin: str | None = None) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    if isinstance(raw, Mapping) and isinstance(raw.get("items"), Mapping):
        return {
            "origin": raw.get("origin") or origin,
            "items": dict(raw["items"]),
        }
    return {
        "origin": origin,
        "items": dict(raw) if isinstance(raw, Mapping) else {},
    }


def write_session_snapshot(
    auth_dir: str | Path,
    storage_state: Mapping[str, Any],
    session_storage: Any = None,
    *,
    session_origin: str | None = None,
) -> None:
    """Write credentials atomically with owner-only permissions."""
    root = Path(auth_dir)
    _write_private_json(root / "state.json", dict(storage_state))
    session = _session_payload(session_storage, session_origin)
    if session["items"]:
        _write_private_json(root / "session-storage.json", session)
    else:
        (root / "session-storage.json").unlink(missing_ok=True)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None


def _storage_init_script(storage_state: Any, session_storage: Any) -> str:
    origins = {}
    if isinstance(storage_state, Mapping):
        for item in storage_state.get("origins") or []:
            if not isinstance(item, Mapping) or not isinstance(item.get("origin"), str):
                continue
            origins[item["origin"]] = {
                str(entry["name"]): str(entry.get("value", ""))
                for entry in item.get("localStorage") or []
                if isinstance(entry, Mapping) and entry.get("name") is not None
            }
    session = _session_payload(session_storage)
    payload = json.dumps(
        {"local": origins, "session": session},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return """(() => {
      const data = JSON.parse(%s);
      try {
        if (Object.prototype.hasOwnProperty.call(data.local, location.origin)) {
          localStorage.clear();
          for (const [key, value] of Object.entries(data.local[location.origin])) {
            localStorage.setItem(key, value);
          }
        }
        if (!data.session.origin || data.session.origin === location.origin) {
          sessionStorage.clear();
          for (const [key, value] of Object.entries(data.session.items || {})) {
            sessionStorage.setItem(key, value);
          }
        }
      } catch { /* inaccessible or quota-limited storage */ }
    })()""" % json.dumps(payload)


def restore_context_session(context: Any, auth_dir: str | Path) -> bool:
    """Restore cookies and web storage before the first replay navigation."""
    root = Path(auth_dir)
    state = _read_json(root / "state.json")
    if not isinstance(state, Mapping):
        return False
    session = _read_json(root / "session-storage.json")
    cookies = state.get("cookies") or []
    if hasattr(context, "clear_cookies"):
        context.clear_cookies()
    if cookies and hasattr(context, "add_cookies"):
        context.add_cookies(list(cookies))
    if hasattr(context, "add_init_script"):
        context.add_init_script(script=_storage_init_script(state, session))
    return True


__all__ = ["restore_context_session", "write_session_snapshot"]
