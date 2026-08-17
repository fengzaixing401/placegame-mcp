import pytest

from placegame.errors import InvalidSecret
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
