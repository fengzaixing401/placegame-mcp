import base64
import os

from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from placegame.config import Settings
from tests.fakes.game_server import FakeGameServer


TEST_MASTER_KEY_B64 = base64.b64encode(bytes(range(32))).decode("ascii")


@pytest.fixture
def fake_game():
    with FakeGameServer() as server:
        yield server


@pytest.fixture
def settings(fake_game: FakeGameServer) -> Settings:
    return Settings(
        test_mode=True,
        game_base_url=fake_game.url,
        master_key_b64=SecretStr(TEST_MASTER_KEY_B64),
    )


@pytest.fixture
def secret_box():
    from placegame.security.crypto import SecretBox

    return SecretBox(TEST_MASTER_KEY_B64)


@pytest.fixture
def postgres_url() -> Iterator[str]:
    explicit_url = os.getenv("PLACEGAME_TEST_DATABASE_URL")
    if explicit_url:
        yield explicit_url
        return

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres.get_connection_url().replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def alembic_config():
    from alembic.config import Config

    def build(database_url: str) -> Config:
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", database_url)
        return config

    return build
