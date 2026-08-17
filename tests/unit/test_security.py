import pytest

from placegame.errors import InvalidSecret
from placegame.contracts import decode_encrypted_secret
from placegame.security.redaction import redact
from placegame.security.tokens import token_digest


def test_secret_box_round_trip_and_aad_binding(secret_box):
    blob = secret_box.encrypt("password", aad="account/1/password")

    assert secret_box.decrypt(blob, aad="account/1/password") == "password"
    with pytest.raises(InvalidSecret):
        secret_box.decrypt(blob, aad="account/2/password")


def test_redaction_removes_credentials_and_authorization():
    assert redact({"password": "p", "Authorization": "Bearer abc", "ok": 1}) == {
        "password": "[REDACTED]",
        "Authorization": "[REDACTED]",
        "ok": 1,
    }


def test_token_digest_is_stable_but_full_token_is_not_stored():
    token = "pgm_" + "a" * 48

    assert token_digest(token) == token_digest(token)
    assert token not in token_digest(token)


def test_encrypted_frame_requires_a_full_gcm_ciphertext(secret_box):
    first = secret_box.encrypt("password", aad="game_accounts/1/password")
    second = secret_box.encrypt("password", aad="game_accounts/1/password")

    assert first.nonce != second.nonce
    with pytest.raises(ValueError, match="encrypted secret format"):
        decode_encrypted_secret(b"\x01" + b"n" * 12 + b"x")


def test_redaction_recurses_before_truncating_values():
    value = {
        "nested": {
            "Authorization": "Bearer do-not-store",
            "description": "x" * 300,
        }
    }

    redacted = redact(value)

    assert redacted["nested"]["Authorization"] == "[REDACTED]"
    assert redacted["nested"]["description"].endswith("...[TRUNCATED]")
