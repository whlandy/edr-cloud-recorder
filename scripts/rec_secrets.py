"""Redact credentials before recorder artifacts are written to disk."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode


REDACTED = "<redacted>"
_SENSITIVE_KEYS = {
    "password", "passwd", "pwd", "secret",
    "accesstoken", "refreshtoken", "authorization", "apikey",
}


def is_sensitive_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized in _SENSITIVE_KEYS


def redact_sensitive_values(value):
    if isinstance(value, dict):
        return {
            key: REDACTED if is_sensitive_key(key) else redact_sensitive_values(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_values(item) for item in value]
    return value


def redact_text(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, (dict, list)):
        return json.dumps(
            redact_sensitive_values(parsed), ensure_ascii=False, separators=(",", ":")
        )

    pairs = parse_qsl(value, keep_blank_values=True)
    if pairs and any(is_sensitive_key(key) for key, _ in pairs):
        return urlencode([
            (key, REDACTED if is_sensitive_key(key) else item)
            for key, item in pairs
        ])
    return value
