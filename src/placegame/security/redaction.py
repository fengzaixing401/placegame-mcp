from collections.abc import Mapping
from typing import Any


_SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "master_key",
)
_MAX_REDACTED_STRING_LENGTH = 256


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]"
            if _is_sensitive_key(key)
            else redact(nested_value)
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str) and len(value) > _MAX_REDACTED_STRING_LENGTH:
        return f"{value[:_MAX_REDACTED_STRING_LENGTH]}...[TRUNCATED]"
    return value


def _is_sensitive_key(key: object) -> bool:
    return isinstance(key, str) and any(part in key.lower() for part in _SENSITIVE_KEY_PARTS)
