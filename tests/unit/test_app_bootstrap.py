from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

import placegame.app as app_module
from placegame.app import create_app
from placegame.config import Settings
from tests.fakes.game_server import FakeGameServer


def with_mcp(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"mcp_token": SecretStr("A" * 43), "mcp_allowed_hosts": ["testserver"]}
    )


async def test_health_endpoint_is_available(settings):
    transport = httpx.ASGITransport(app=create_app(with_mcp(settings)))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_has_health_then_one_root_mcp_fallback(settings):
    app = create_app(with_mcp(settings))
    assert [(route.path, route.name) for route in app.routes] == [
        ("/health/live", "live"),
        ("/health/ready", "ready"),
        ("", "mcp"),
    ]
    assert app.docs_url is app.redoc_url is app.openapi_url is None


class LifecycleServer:
    def __init__(self, events: list[str], *, startup_error: Exception | None = None) -> None:
        self.events = events
        self.startup_error = startup_error
        self.session_manager = SimpleNamespace(run=self.run)

    @asynccontextmanager
    async def run(self):
        self.events.append("mcp start")
        if self.startup_error is not None:
            raise self.startup_error
        try:
            yield
        finally:
            self.events.append("mcp stop")

    async def child(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise AssertionError("MCP child must not receive an unauthorized request")

    def streamable_http_app(self):
        return self.child


class CloseObserver:
    def __init__(self, events: list[str], event: str) -> None:
        self.events = events
        self.event = event

    async def aclose(self) -> None:
        self.events.append(self.event)


def install_lifecycle_server(monkeypatch: pytest.MonkeyPatch, events: list[str], **kwargs: Any) -> None:
    server = LifecycleServer(events, **kwargs)
    monkeypatch.setattr(app_module, "create_mcp_server", lambda *args, **_kwargs: server, raising=False)


async def test_unauthenticated_mcp_does_not_enter_mcp_lifespan(settings, monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []
    install_lifecycle_server(monkeypatch, events)
    transport = httpx.ASGITransport(app=create_app(with_mcp(settings)))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/mcp")

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert events == []


async def test_parent_lifespan_owns_mcp_then_closes_http_and_database_once(settings, monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []
    install_lifecycle_server(monkeypatch, events)
    app = create_app(with_mcp(settings))
    app.state.http_client = CloseObserver(events, "HTTP close")
    app.state.database = CloseObserver(events, "database close")

    async with app.router.lifespan_context(app):
        pass

    assert events == ["mcp start", "mcp stop", "HTTP close", "database close"]


async def test_mcp_startup_failure_still_closes_http_and_database_once(settings, monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []
    install_lifecycle_server(monkeypatch, events, startup_error=RuntimeError("startup failed"))
    app = create_app(with_mcp(settings))
    app.state.http_client = CloseObserver(events, "HTTP close")
    app.state.database = CloseObserver(events, "database close")

    with pytest.raises(RuntimeError, match="startup failed"):
        async with app.router.lifespan_context(app):
            pass

    assert events == ["mcp start", "HTTP close", "database close"]


def test_settings_reads_database_url_from_a_nonempty_secret_file(tmp_path: Path):
    secret_file = tmp_path / "database-url"
    secret_file.write_text("postgresql+asyncpg://secret-host/database\n", encoding="utf-8")

    settings = Settings(database_url_file=secret_file)

    assert settings.read_database_url() == "postgresql+asyncpg://secret-host/database"


def test_settings_rejects_non_loopback_game_urls_in_test_mode():
    with pytest.raises(ValidationError, match="test game_base_url must be loopback"):
        Settings(test_mode=True, game_base_url="https://game.placegame.cn")


def test_fake_server_serves_only_registered_api_routes_and_redacts_authorization():
    with FakeGameServer() as fake_game:
        fake_game.register("GET", "/api/client/bootstrap", {"data": {"ready": True}})

        registered = httpx.get(
            f"{fake_game.url}/api/client/bootstrap",
            headers={"Authorization": "Bearer never-store-this"},
        )
        unregistered = httpx.get(f"{fake_game.url}/api/delete-all")

    assert registered.json() == {"data": {"ready": True}}
    assert unregistered.status_code == 404
    assert fake_game.requests[0].headers["authorization"] == "[REDACTED]"
    assert "never-store-this" not in repr(fake_game.requests[0])
