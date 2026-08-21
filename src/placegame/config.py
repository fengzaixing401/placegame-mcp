from pathlib import Path
from urllib.parse import urlparse

import re
from uuid import uuid4

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from placegame.mcp.auth import validate_static_token


_ALLOWED_HOST = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\])(?::\*)?\Z",
    re.ASCII,
)
_DEFAULT_SCHEDULER_WORKER_ID = uuid4().hex


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="forbid", populate_by_name=True
    )

    database_url: str = "postgresql+asyncpg://placegame:placegame@postgres:5432/placegame"
    database_url_file: Path | None = Field(None, alias="PLACEGAME_DATABASE_URL_FILE")
    game_base_url: str = "https://game.placegame.cn"
    test_mode: bool = False
    master_key_b64: SecretStr | None = Field(None, alias="PLACEGAME_MASTER_KEY_B64")
    master_key_file: Path = Field(
        Path("/run/secrets/placegame_master_key"), alias="PLACEGAME_MASTER_KEY_FILE"
    )
    mcp_token: SecretStr | None = Field(None, alias="PLACEGAME_MCP_TOKEN")
    mcp_token_file: Path = Field(
        Path("/run/secrets/placegame_mcp_token"), alias="PLACEGAME_MCP_TOKEN_FILE"
    )
    mcp_allowed_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1:*", "localhost:*", "[::1]:*"],
        alias="PLACEGAME_MCP_ALLOWED_HOSTS",
    )
    admin_cookie_secure: bool = Field(True, alias="PLACEGAME_ADMIN_COOKIE_SECURE")
    scheduler_interval_seconds: int = 300
    scheduler_lease_seconds: int = 30
    scheduler_worker_id: str = _DEFAULT_SCHEDULER_WORKER_ID
    max_account_concurrency: int = 4
    audit_retention_days: int = 90

    @model_validator(mode="after")
    def fixed_game_origin(self) -> "Settings":
        if (
            not self.test_mode
            and self.game_base_url.rstrip("/") != "https://game.placegame.cn"
        ):
            raise ValueError("production game_base_url must be https://game.placegame.cn")
        if self.test_mode and urlparse(self.game_base_url).hostname not in {
            "127.0.0.1",
            "localhost",
            "testserver",
            "::1",
        }:
            raise ValueError("test game_base_url must be loopback")
        return self

    @field_validator("mcp_allowed_hosts")
    @classmethod
    def validate_mcp_allowed_hosts(cls, value: list[str]) -> list[str]:
        if not value or any(_ALLOWED_HOST.fullmatch(host) is None for host in value):
            raise ValueError("MCP allowed hosts must be exact ASCII hosts or host:*")
        return value

    @classmethod
    def from_env(cls) -> "Settings":
        return cls.model_validate({})

    def read_database_url(self) -> str:
        if self.database_url_file is None:
            return self.database_url
        value = self.database_url_file.read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError("database URL secret file is empty")
        return value

    def read_master_key_b64(self) -> SecretStr:
        if self.master_key_b64 is not None:
            return self.master_key_b64
        return SecretStr(self.master_key_file.read_text(encoding="ascii").strip())

    def read_mcp_token(self) -> SecretStr:
        if self.mcp_token is not None:
            return SecretStr(validate_static_token(self.mcp_token.get_secret_value()))
        try:
            value = self.mcp_token_file.read_bytes().decode("ascii")
            if value.endswith("\r\n"):
                value = value.removesuffix("\r\n")
            elif value.endswith("\n"):
                value = value.removesuffix("\n")
            return SecretStr(validate_static_token(value))
        except (OSError, UnicodeDecodeError, ValueError):
            raise ValueError("MCP token secret is unavailable") from None
