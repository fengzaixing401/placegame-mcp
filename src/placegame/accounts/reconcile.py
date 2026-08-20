import base64
import binascii
import json
import math
from collections.abc import Callable
from datetime import datetime, timezone


TokenExpiryResolver = Callable[[str], datetime | None]


def default_token_expiry(token: str) -> datetime | None:
    """Read an optional JWT ``exp`` hint without treating it as authentication."""

    if not isinstance(token, str) or not token or len(token) > 8192:
        return None
    parts = token.split(".")
    if len(parts) != 3 or not parts[1] or len(parts[1]) > 4096:
        return None
    try:
        encoded = parts[1].encode("ascii")
        encoded += b"=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded))
    except (ValueError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("exp")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
