from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]


def compose() -> dict:
    return yaml.safe_load((ROOT / "deploy/compose.yaml").read_text(encoding="utf-8"))


def test_image_contains_migration_runtime() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY alembic.ini ./" in dockerfile
    assert "COPY migrations ./migrations" in dockerfile
    assert "WORKDIR /app" in dockerfile


def test_compose_has_only_placegame_services_and_ports() -> None:
    model = compose()
    services = model["services"]
    assert model["name"] == "placegame-mcp"
    assert set(services) == {"app", "migrate", "postgres"}
    assert services["app"]["ports"] == ["127.0.0.1:18080:8000"]
    assert "ports" not in services["postgres"]
    assert services["postgres"]["image"] == "postgres:16-alpine"
    assert "subboost" not in (ROOT / "deploy/compose.yaml").read_text().lower()


def test_compose_uses_secret_files_and_explicit_migration_command() -> None:
    services = compose()["services"]
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert services["migrate"]["working_dir"] == "/app"
    assert services["postgres"]["environment"] == {
        "POSTGRES_DB": "placegame",
        "POSTGRES_USER": "placegame",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres_password",
    }
    expected = {
        "PLACEGAME_DATABASE_URL_FILE": "/run/secrets/database_url",
        "PLACEGAME_MASTER_KEY_FILE": "/run/secrets/placegame_master_key",
        "PLACEGAME_MCP_TOKEN_FILE": "/run/secrets/placegame_mcp_token",
    }
    assert services["app"]["environment"] == expected
    assert services["migrate"]["environment"] == expected
