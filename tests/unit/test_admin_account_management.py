from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from placegame.admin.auth import AdminAuthService
from placegame.admin.routes import create_admin_router
from placegame.accounts.service import ManagedAccount, RemovalReceipt
from placegame.contracts import Actor


class MemoryAuthStore:
    def __init__(self) -> None:
        self.password_hash: str | None = None
        self.sessions = {}

    async def setup(self, password_hash, now):
        if self.password_hash is not None:
            return False
        self.password_hash = password_hash
        return True

    async def read_password_hash(self):
        return self.password_hash

    async def create_session(self, token_digest, now, absolute_expires_at):
        from placegame.models import AdminSession

        record = AdminSession(
            token_digest=token_digest,
            created_at=now,
            absolute_expires_at=absolute_expires_at,
            last_seen_at=now,
        )
        self.sessions[token_digest] = record
        return record

    async def find_session(self, token_digest, now, idle_seconds):
        record = self.sessions.get(token_digest)
        if record is None:
            return None
        record.last_seen_at = now
        return record

    async def delete_session(self, token_digest):
        self.sessions.pop(token_digest, None)


class AccountServiceFake:
    def __init__(self) -> None:
        self.account_id = uuid4()
        self.calls: list[tuple[str, tuple, dict]] = []
        self.account = ManagedAccount(
            id=self.account_id,
            label="alpha",
            auth_mode="credentials",
            enabled=True,
            paused_reason=None,
            session_expires_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
            created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
            updated_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return self.account

    async def add_credentials(self, *args, **kwargs):
        self.account = replace(self.account, auth_mode="credentials")
        return self._record("add_credentials", *args, **kwargs)

    async def add_token_only(self, *args, **kwargs):
        self.account = replace(self.account, auth_mode="token_only")
        return self._record("add_token_only", *args, **kwargs)

    async def update_label(self, *args, **kwargs):
        return self._record("update_label", *args, **kwargs)

    async def update_credentials(self, *args, **kwargs):
        self.account = replace(self.account, auth_mode="credentials")
        return self._record("update_credentials", *args, **kwargs)

    async def update_token_only(self, *args, **kwargs):
        self.account = replace(self.account, auth_mode="token_only")
        return self._record("update_token_only", *args, **kwargs)

    async def enable(self, *args, **kwargs):
        return self._record("enable", *args, **kwargs)

    async def disable(self, *args, **kwargs):
        return self._record("disable", *args, **kwargs)

    async def pause(self, *args, **kwargs):
        return self._record("pause", *args, **kwargs)

    async def resume(self, *args, **kwargs):
        return self._record("resume", *args, **kwargs)

    async def disable_drain_remove(self, *args, **kwargs):
        self._record("disable_drain_remove", *args, **kwargs)
        return RemovalReceipt(self.account_id, datetime.now(timezone.utc), 0)

    async def get(self, *_args, **_kwargs):
        return self.account

    async def list_accounts(self):
        return (self.account,)


def build_app():
    app = FastAPI()
    app.state.admin_auth = AdminAuthService(MemoryAuthStore())
    app.state.account_service = AccountServiceFake()
    app.include_router(create_admin_router(cookie_secure=False))
    return app, app.state.account_service


@pytest.fixture
async def account_client():
    app, service = build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/admin/v1/auth/setup", json={"password": "a" * 14})
        await client.post("/api/admin/v1/auth/login", json={"password": "a" * 14})
        yield client, service


def assert_public(response: httpx.Response, secret: str) -> None:
    assert response.status_code in {200, 201}
    if secret:
        assert secret not in response.text
    body = response.json()
    assert body["account"]["label"] == "alpha"
    assert "password" not in response.text.lower()
    assert "session_token" not in response.text.lower()


async def test_account_creation_and_same_mode_updates_are_authenticated(account_client):
    client, service = account_client
    credentials = await client.post(
        "/api/admin/v1/accounts/credentials",
        json={"label": "alpha", "username": "player", "password": "secret-password"},
    )
    assert_public(credentials, "secret-password")
    assert service.calls[-1][0] == "add_credentials"
    assert service.calls[-1][2]["actor"] == Actor("webui", "operator", frozenset())
    label = await client.patch(
        f"/api/admin/v1/accounts/{service.account_id}/label", json={"label": "renamed"}
    )
    assert_public(label, "renamed")
    credentials_update = await client.patch(
        f"/api/admin/v1/accounts/{service.account_id}/credentials",
        json={"username": "player2", "password": "new-secret"},
    )
    assert_public(credentials_update, "new-secret")

    token = await client.post(
        "/api/admin/v1/accounts/token-only",
        json={"label": "alpha", "sessionToken": "opaque-session-token"},
    )
    assert_public(token, "opaque-session-token")
    assert service.calls[-1][0] == "add_token_only"
    token_update = await client.patch(
        f"/api/admin/v1/accounts/{service.account_id}/token-only",
        json={"sessionToken": "new-opaque-token"},
    )
    assert_public(token_update, "new-opaque-token")


async def test_account_list_includes_auth_mode_without_secret_fields(account_client):
    client, _ = account_client
    response = await client.get("/api/admin/v1/accounts")
    assert response.status_code == 200
    assert response.json()[0]["auth_mode"] == "credentials"
    assert "password" not in response.text.lower()
    assert "session_token" not in response.text.lower()


async def test_credential_updates_cannot_change_existing_auth_mode(account_client):
    client, service = account_client
    service.account = replace(service.account, auth_mode="token_only")
    credentials = await client.patch(
        f"/api/admin/v1/accounts/{service.account_id}/credentials",
        json={"password": "do-not-convert"},
    )
    assert credentials.status_code == 409
    assert credentials.json() == {"error": "account_auth_mode_conflict"}
    assert "do-not-convert" not in credentials.text

    service.account = replace(service.account, auth_mode="credentials")
    token = await client.patch(
        f"/api/admin/v1/accounts/{service.account_id}/token-only",
        json={"sessionToken": "do-not-convert-token"},
    )
    assert token.status_code == 409
    assert token.json() == {"error": "account_auth_mode_conflict"}
    assert "do-not-convert-token" not in token.text


@pytest.mark.parametrize("action", ["enable", "disable", "resume"])
async def test_lifecycle_actions_delegate_to_account_service(account_client, action):
    client, service = account_client
    response = await client.post(
        f"/api/admin/v1/accounts/{service.account_id}/{action}", json={}
    )
    assert_public(response, "")
    assert service.calls[-1][0] == action
    assert service.calls[-1][2]["actor"] == Actor("webui", "operator", frozenset())


async def test_pause_and_remove_delegate_to_service(account_client):
    client, service = account_client
    paused = await client.post(
        f"/api/admin/v1/accounts/{service.account_id}/pause", json={"reason": "operator"}
    )
    assert_public(paused, "")
    assert service.calls[-1][0] == "pause"
    removed = await client.request(
        "DELETE", f"/api/admin/v1/accounts/{service.account_id}", json={}
    )
    assert_public(removed, "")
    assert service.calls[-1][0] == "disable_drain_remove"


async def test_account_management_rejects_missing_cookie_and_secret_payload_errors():
    app, _ = build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        denied = await client.post(
            "/api/admin/v1/accounts/credentials",
            json={"label": "alpha", "username": "u", "password": "do-not-echo"},
        )
        invalid = await client.post(
            "/api/admin/v1/accounts/credentials",
            json={"label": "alpha", "username": "u", "password": "do-not-echo", "extra": "x"},
        )
    assert denied.status_code == 401
    assert invalid.status_code == 422
    assert "do-not-echo" not in denied.text + invalid.text
