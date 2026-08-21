from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from placegame.admin.auth import AdminAuthService
from placegame.admin.routes import create_admin_router
from placegame.application.models import AccountStatus, AccountSummary, IdlePreview, IdleState
from placegame.contracts import Actor
from placegame.models import AdminSession


class MemoryAuthStore:
    def __init__(self) -> None:
        self.password_hash: str | None = None
        self.sessions: dict[str, AdminSession] = {}

    async def setup(self, password_hash: str, now: datetime) -> bool:
        if self.password_hash is not None:
            return False
        self.password_hash = password_hash
        return True

    async def read_password_hash(self) -> str | None:
        return self.password_hash

    async def update_password_hash(self, password_hash: str, now: datetime) -> None:
        self.password_hash = password_hash

    async def create_session(
        self, token_digest: str, now: datetime, absolute_expires_at: datetime
    ) -> AdminSession:
        record = AdminSession(
            token_digest=token_digest,
            created_at=now,
            absolute_expires_at=absolute_expires_at,
            last_seen_at=now,
        )
        self.sessions[token_digest] = record
        return record

    async def find_session(
        self, token_digest: str, now: datetime, idle_seconds: int
    ) -> AdminSession | None:
        record = self.sessions.get(token_digest)
        if record is None:
            return None
        if record.absolute_expires_at <= now or record.last_seen_at + timedelta(seconds=idle_seconds) <= now:
            self.sessions.pop(token_digest, None)
            return None
        record.last_seen_at = now
        return record

    async def delete_session(self, token_digest: str) -> None:
        self.sessions.pop(token_digest, None)

    async def delete_all_sessions(self) -> None:
        self.sessions.clear()


class StatusFake:
    def __init__(self) -> None:
        self.account_id = uuid4()
        self.status_actors: list[Actor] = []

    async def list(self) -> tuple[AccountSummary, ...]:
        return (
            AccountSummary(
                account_id=self.account_id,
                label="alpha",
                enabled=True,
                paused_reason=None,
                auth_state="unknown",
            ),
        )

    async def get(self, account_id: UUID, *, actor: Actor) -> AccountStatus:
        self.status_actors.append(actor)
        return AccountStatus(
            account=(await self.list())[0],
            bootstrap_account_id="game-alpha",
            idle=IdleState(accumulatedSeconds=1, capacitySeconds=2),
            fetched_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )


class PreviewFake:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, Actor, str]] = []

    async def preview(
        self, account_id: UUID, *, actor: Actor, correlation_id: str
    ) -> IdlePreview:
        self.calls.append((account_id, actor, correlation_id))
        return IdlePreview(
            account_id=account_id,
            plan_id=None,
            decision="wait",
            accumulated_seconds=1,
            capacity_seconds=2,
            threshold_seconds=2,
            expires_at=None,
            reason="idle_threshold_not_reached",
            correlation_id=correlation_id,
        )


def build_app(*, cookie_secure: bool = False):
    app = FastAPI()
    auth = AdminAuthService(MemoryAuthStore())
    status = StatusFake()
    preview = PreviewFake()
    app.state.admin_auth = auth
    app.state.account_status_query = status
    app.state.idle_plan_use_case = preview
    app.include_router(create_admin_router(cookie_secure=cookie_secure))
    return app, auth, status, preview


@pytest.fixture
async def client():
    app, auth, status, preview = build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http, auth, status, preview


async def test_auth_setup_status_login_and_logout_cookie_boundary(client):
    http, _, _, _ = client

    status = await http.get("/api/admin/v1/auth/status")
    assert status.status_code == 200
    assert status.json() == {"setupRequired": True, "authenticated": False}

    setup = await http.post("/api/admin/v1/auth/setup", json={"password": "a" * 14})
    assert setup.status_code == 201
    assert setup.json() == {"setupRequired": False, "authenticated": False}

    login = await http.post("/api/admin/v1/auth/login", json={"password": "a" * 14})
    assert login.status_code == 200
    assert login.json() == {"authenticated": True}
    cookie = http.cookies.get("placegame_session")
    assert cookie is not None
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "samesite=strict" in login.headers["set-cookie"].lower()
    assert "Secure" not in login.headers["set-cookie"]

    logout = await http.post("/api/admin/v1/auth/logout", json={})
    assert logout.status_code == 204
    assert "Max-Age=0" in logout.headers["set-cookie"]


async def test_change_password_requires_a_session_and_the_current_password(client):
    http, _, _, _ = client

    denied = await http.patch(
        "/api/admin/v1/auth/password",
        json={"currentPassword": "a" * 14, "newPassword": "second"},
    )
    assert denied.status_code == 401
    assert denied.json() == {"error": "unauthorized"}

    await http.post("/api/admin/v1/auth/setup", json={"password": "a" * 14})
    await http.post("/api/admin/v1/auth/login", json={"password": "a" * 14})

    wrong = await http.patch(
        "/api/admin/v1/auth/password",
        json={"currentPassword": "wrong", "newPassword": "second"},
    )
    assert wrong.status_code == 401
    assert wrong.json() == {"error": "unauthorized"}


async def test_change_password_clears_the_cookie_and_reports_a_blank_new_password(client):
    http, auth, _, _ = client

    await http.post("/api/admin/v1/auth/setup", json={"password": "a" * 14})
    await http.post("/api/admin/v1/auth/login", json={"password": "a" * 14})

    blank = await http.patch(
        "/api/admin/v1/auth/password",
        json={"currentPassword": "a" * 14, "newPassword": "   "},
    )
    assert blank.status_code == 422
    assert blank.json() == {"error": "password_too_short"}

    changed = await http.patch(
        "/api/admin/v1/auth/password",
        json={"currentPassword": "a" * 14, "newPassword": "x"},
    )
    assert changed.status_code == 204
    assert "Max-Age=0" in changed.headers["set-cookie"]
    assert auth.store.sessions == {}

    assert (await http.get("/api/admin/v1/accounts")).status_code == 401
    relogin = await http.post("/api/admin/v1/auth/login", json={"password": "x"})
    assert relogin.status_code == 200


async def test_protected_routes_reject_missing_cookie_and_delegate_with_fixed_actor(client):
    http, _, status, preview = client
    account_id = status.account_id

    denied = await http.get("/api/admin/v1/accounts")
    assert denied.status_code == 401
    assert denied.json() == {"error": "unauthorized"}
    bearer_only = await http.get(
        "/api/admin/v1/accounts", headers={"Authorization": f"Bearer {'A' * 43}"}
    )
    assert bearer_only.status_code == 401

    await http.post("/api/admin/v1/auth/setup", json={"password": "a" * 14})
    await http.post("/api/admin/v1/auth/login", json={"password": "a" * 14})
    listed = await http.get("/api/admin/v1/accounts")
    detail = await http.get(f"/api/admin/v1/accounts/{account_id}/status")
    idle = await http.get(f"/api/admin/v1/accounts/{account_id}/idle-preview")

    assert listed.status_code == 200
    assert detail.json()["account"]["account_id"] == str(account_id)
    assert detail.json()["idle"]["accumulatedSeconds"] == 1
    assert idle.json()["decision"] == "wait"
    assert status.status_actors == [Actor("webui", "operator", frozenset())]
    assert preview.calls[0][1] == Actor("webui", "operator", frozenset())
    assert len(preview.calls[0][2]) == 32


async def test_unknown_admin_route_is_json_not_mcp_fallback(client):
    http, _, _, _ = client
    response = await http.get("/api/admin/v1/no-such-route")
    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}


async def test_wrong_password_and_cross_origin_write_have_stable_errors(client):
    http, _, _, _ = client
    wrong = await http.post("/api/admin/v1/auth/login", json={"password": "wrong-password"})
    cross_origin = await http.post(
        "/api/admin/v1/auth/setup",
        json={"password": "a" * 14},
        headers={"Origin": "https://other.example"},
    )

    assert wrong.status_code == 401
    assert wrong.json() == {"error": "unauthorized"}
    assert cross_origin.status_code == 403
    assert cross_origin.json() == {"error": "origin_forbidden"}


async def test_same_host_origin_is_allowed_when_proxy_terminates_tls(client):
    http, _, _, _ = client
    response = await http.post(
        "/api/admin/v1/auth/setup",
        json={"password": "a" * 14},
        headers={"Origin": "https://testserver"},
    )
    assert response.status_code == 201


async def test_production_cookie_default_is_secure():
    app, _, _, _ = build_app(cookie_secure=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://testserver"
    ) as http:
        await http.post("/api/admin/v1/auth/setup", json={"password": "a" * 14})
        login = await http.post(
            "/api/admin/v1/auth/login", json={"password": "a" * 14}
        )

    assert "Secure" in login.headers["set-cookie"]


async def test_account_errors_are_safely_mapped(client):
    from placegame.errors import AccountNotFound

    http, _, status, _ = client
    await http.post("/api/admin/v1/auth/setup", json={"password": "a" * 14})
    await http.post("/api/admin/v1/auth/login", json={"password": "a" * 14})

    async def missing(account_id, *, actor):
        raise AccountNotFound()

    status.get = missing
    response = await http.get(f"/api/admin/v1/accounts/{uuid4()}/status")

    assert response.status_code == 404
    assert response.json() == {"error": "account_not_found"}


async def test_auth_storage_failures_are_stable_json_errors():
    class FailingAuth:
        async def is_setup(self):
            raise RuntimeError("database detail must not escape")

        async def validate(self, _token):
            raise RuntimeError("database detail must not escape")

        async def logout(self, _token):
            raise RuntimeError("database detail must not escape")

    app, _, _, _ = build_app()
    app.state.admin_auth = FailingAuth()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as http:
        status = await http.get("/api/admin/v1/auth/status")
        http.cookies.set("placegame_session", "A" * 43)
        accounts = await http.get("/api/admin/v1/accounts")
        logout = await http.post("/api/admin/v1/auth/logout", json={})

    for response in (status, accounts, logout):
        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"error": "internal_error"}
        assert "database detail" not in response.text


async def test_invalid_auth_payload_does_not_echo_secret_values(client):
    http, _, _, _ = client
    secret = "DO_NOT_ECHO"
    responses = (
        await http.post(
            "/api/admin/v1/auth/setup",
            json={"password": "a" * 14, "extra": secret},
        ),
        await http.post(
            "/api/admin/v1/auth/login",
            json={"password": {"secret": secret}},
        ),
    )

    for response in responses:
        assert response.status_code == 422
        assert response.json() == {"error": "invalid_request"}
        assert secret not in response.text
