import json
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from placegame.game.client import _interpret_response
from placegame.game.schemas import BootstrapState, IdleCollectResult, IdleSummary
from placegame.security.redaction import redact


FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures" / "game" / "v1"


@pytest.mark.parametrize(
    ("filename", "endpoint", "schema", "shape_verified"),
    [
        ("bootstrap.json", "/api/client/bootstrap", BootstrapState, True),
        ("idle-summary.json", "/api/client/idle-summary", IdleSummary, True),
        ("idle-collect.json", "/api/client/collect", IdleCollectResult, True),
    ],
)
def test_idle_contract_fixture_is_synthetic_schema_valid_and_redacted(
    filename: str, endpoint: str, schema: type[BaseModel], shape_verified: bool
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

    if shape_verified:
        assert document["shape_verified_at"] == "2026-08-22T00:00:00Z"
        assert document["shape_source"] == "redacted HAR capture of the live web client"
    else:
        assert document["shape_verified_at"] is None
        assert document["shape_source"] is None

    # Decoded through the client so the fixture proves the real parsing path, not a
    # second implementation of it.
    decoded = _interpret_response(
        httpx.Response(200, json=document["response"]), schema
    )
    assert isinstance(decoded, schema)


def test_a_business_failure_envelope_is_not_reported_as_a_broken_contract() -> None:
    """A 200 carrying ok:false is an ordinary refusal, not a schema mismatch."""

    decoded = _interpret_response(
        httpx.Response(200, json={"ok": False, "error": "背包已满。"}), IdleCollectResult
    )

    assert not isinstance(decoded, IdleCollectResult)
    assert decoded.kind == "conflict"


def test_a_mutation_result_envelope_is_unwrapped_to_its_result() -> None:
    """Mutations answer with data = {"result": ..., "statePatch": ...}.

    The shape mirrors a live collection: the payload sits under `result`, with a
    sibling `statePatch` and an envelope-level `changedSections`.
    """

    decoded = _interpret_response(
        httpx.Response(
            200,
            json={
                "ok": True,
                "data": {
                    "result": {"rewardPreview": {"gold": 7}, "adventure": None},
                    "statePatch": {"player": {"gold": 7}},
                },
                "changedSections": ["player"],
            },
        ),
        IdleCollectResult,
    )

    assert isinstance(decoded, IdleCollectResult)
    extra = decoded.model_extra or {}
    assert extra["rewardPreview"] == {"gold": 7}
    # statePatch belongs to the envelope, not the operation's own payload.
    assert "statePatch" not in extra
