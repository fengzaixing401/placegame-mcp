from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from placegame.app import create_app
from placegame.config import Settings
from tests.fakes.game_server import FakeGameServer


async def test_health_endpoint_is_available(settings):
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
