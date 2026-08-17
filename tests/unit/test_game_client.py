import httpx
import pytest

from placegame.errors import (
    AmbiguousMutation,
    ContractChanged,
    GameConflict,
    GameRateLimited,
    GameUnavailable,
    InsufficientResource,
    InventoryFull,
    SessionRejected,
)
from placegame.game.client import HttpGameClient
from placegame.game.registry import REGISTRY


@pytest.fixture
async def game_client(settings):
    async with httpx.AsyncClient() as http:
        yield HttpGameClient(
            settings,
            session_token="test-session-token",
            http_client=http,
            timeout=0.02,
            request_spacing_seconds=0,
        )


async def test_typed_request_adds_bearer_and_redacts_recorded_headers(fake_game, game_client):
    fake_game.register("GET", "/api/client/bootstrap", {"data": {"ready": True}})

    await game_client.bootstrap()

    request = fake_game.requests[-1]
    assert request.path == "/api/client/bootstrap"
    assert request.headers["authorization"] == "[REDACTED]"


async def test_login_has_no_bearer_and_one_transport_attempt(fake_game, settings):
    fake_game.register("POST", "/api/auth/login", {"data": {"token": "new-token"}})
    async with httpx.AsyncClient() as http:
        client = HttpGameClient(
            settings,
            session_token="old-token",
            http_client=http,
            request_spacing_seconds=0,
        )
        await client.login("user", "password")

    request = fake_game.requests[-1]
    assert "authorization" not in request.headers
    assert request.json_body == {"username": "user", "password": "password"}
    assert len(fake_game.requests) == 1


async def test_unknown_operation_cannot_be_called(game_client):
    assert not hasattr(game_client, "raw")
    assert "/api/delete-all" not in {spec.path for spec in REGISTRY.values()}


async def test_safe_reward_claims_have_fixed_paths_and_bodies(fake_game, game_client):
    fake_game.register("POST", "/api/quests/claim", {"data": {}})

    await game_client.quest_claim("quest-1")

    assert fake_game.requests[-1].path == "/api/quests/claim"
    assert fake_game.requests[-1].json_body == {"questKey": "quest-1"}
    assert "mail_claim_all" not in REGISTRY


async def test_read_timeout_retries_three_times_but_mutation_timeout_is_ambiguous(
    fake_game, game_client
):
    fake_game.register("GET", "/api/client/catalog", {"data": {}})
    fake_game.fail_next_reads = 3
    with pytest.raises(GameUnavailable):
        await game_client.catalog()
    assert [request.path for request in fake_game.requests] == [
        "/api/client/catalog",
        "/api/client/catalog",
        "/api/client/catalog",
    ]

    fake_game.register("POST", "/api/battle/idle-collect", {"data": {}})
    fake_game.timeout_next_mutation = True
    with pytest.raises(AmbiguousMutation):
        await game_client.idle_collect()
    assert [request.path for request in fake_game.requests[-1:]] == [
        "/api/battle/idle-collect"
    ]


@pytest.mark.parametrize(
    ("status_code", "body", "error"),
    [
        (401, {"error": {"code": "session_rejected"}}, SessionRejected),
        (426, {"error": {"code": "upgrade_required"}}, ContractChanged),
        (400, {"error": {"code": "inventory_full"}}, InventoryFull),
        (400, {"error": {"code": "insufficient_resource"}}, InsufficientResource),
        (409, {"error": {"code": "conflict"}}, GameConflict),
        (429, {"error": {"code": "rate_limited"}}, GameRateLimited),
    ],
)
async def test_error_envelopes_map_to_stable_typed_errors(
    fake_game, game_client, status_code, body, error
):
    fake_game.register("GET", "/api/client/catalog", body, status_code=status_code)

    with pytest.raises(error):
        await game_client.catalog()


async def test_mutation_5xx_is_ambiguous_and_is_not_retried(fake_game, game_client):
    fake_game.register("POST", "/api/battle/idle-collect", {"error": {}}, status_code=503)

    with pytest.raises(AmbiguousMutation):
        await game_client.idle_collect()

    assert [request.path for request in fake_game.requests] == ["/api/battle/idle-collect"]
