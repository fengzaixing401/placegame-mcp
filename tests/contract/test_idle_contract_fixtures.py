import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from placegame.game.schemas import BootstrapState, IdleCollectResult, IdleSummary
from placegame.security.redaction import redact


FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures" / "game" / "v1"


@pytest.mark.parametrize(
    ("filename", "endpoint", "schema"),
    [
        ("bootstrap.json", "/api/client/bootstrap", BootstrapState),
        ("idle-summary.json", "/api/client/idle-summary", IdleSummary),
        ("idle-collect.json", "/api/battle/idle-collect", IdleCollectResult),
    ],
)
def test_idle_contract_fixture_is_synthetic_schema_valid_and_redacted(
    filename: str, endpoint: str, schema: type[BaseModel]
) -> None:
    document = json.loads((FIXTURE_DIRECTORY / filename).read_text(encoding="utf-8"))

    assert document["provenance"] == "synthetic"
    assert document["endpoint"] == endpoint
    assert document["created_at"] == "2026-08-19T00:00:00Z"
    assert document["verified_at"] is None
    assert document["game_contract_version"] == "unverified"
    assert document["redaction_method"] == "placegame.security.redaction.redact"
    assert document["live_contract_status"] == "unverified"
    assert redact(document) == document
    schema.model_validate(document["data"])
