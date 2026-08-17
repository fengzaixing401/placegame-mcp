from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    scheduler_lease_seconds: int = 30
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

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()

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
