import inspect
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from placegame.errors import (
    AmbiguousMutation,
    ContractChanged,
    GameConflict,
    GameError,
    GameHttpError,
    GameRateLimited,
    GameSchemaMismatch,
    GameUnavailable,
    InsufficientResource,
    InventoryFull,
    SessionRejected,
)
from placegame.game.client import HttpGameClient
from placegame.game.registry import EndpointSpec, REGISTRY
from placegame.game.schemas import (
    BootstrapState,
    BossChallengeRequest,
    BossPreviewRequest,
    GameUser,
)


VALID_BOSS_DIFFICULTY = {
    "key": "hard",
    "predictedWin": True,
    "chance": 88.0,
    "playerHpRemainingPercent": 61.5,
    "bossHpRemainingPercent": 0.0,
}
VALID_PERSONAL_BOSS = {
    "key": "personal-boss-1",
    "type": "personal",
    "blockedReason": None,
    "difficultyOptions": [VALID_BOSS_DIFFICULTY],
    "personalAttemptPool": {
        "freeRemaining": 2,
        "freeLimit": 3,
        "ticketUsed": 0,
        "ticketLimit": 2,
    },
}
VALID_MAP_BOSS = {
    "key": "map-boss-1",
    "type": "map",
    "attempts": 2,
    "blockedReason": None,
    "difficultyOptions": [VALID_BOSS_DIFFICULTY],
}
VALID_WORLD_BOSS = {
    "key": "world-boss-1",
    "type": "world",
    "blockedReason": "not_active",
    "difficultyOptions": [VALID_BOSS_DIFFICULTY],
    "refreshText": "opens at 20:00",
}


VALID_RESPONSES: dict[str, dict[str, Any]] = {
    "login": {"sessionToken": "new-session-token", "expiresAt": 1787000000},
    "bootstrap": {"player": {"id": "account-1"}},
    "catalog": {"qualities": [], "jobs": [], "items": []},
    "idle_summary": {"validSeconds": 3600.0},
    "view_sections": {
        "sectionEtags": {"bosses": "bosses-etag"},
        "bosses": [VALID_PERSONAL_BOSS, VALID_MAP_BOSS, VALID_WORLD_BOSS],
    },
    "idle_collect": {"collected": True},
    "boss_preview": {
        "predictedWin": True,
        "chance": 87.5,
        "playerHpRemainingPercent": 63.0,
        "bossHpRemainingPercent": 0.0,
    },
    "boss_challenge": {"won": True},
    "boss_assist": {"myAttemptCount": 1, "remainingAttemptCount": 2},
    "profession_settle": {"selectedProfessionKey": "smith", "queueSize": 1},
    "profession_enqueue": {"queueSize": 2},
    "profession_supply_equip": {"equipped": True},
    "reward_claim": {"claimed": True},
}


# Identity is reported outside `data`, so bootstrap needs an envelope-level user.
VALID_ENVELOPE_FIELDS: dict[str, dict[str, Any]] = {
    "bootstrap": {"user": {"id": "account-1"}},
}


def valid_envelope(response_key: str) -> dict[str, Any]:
    envelope: dict[str, Any] = {"ok": True, "data": VALID_RESPONSES[response_key]}
    envelope.update(VALID_ENVELOPE_FIELDS.get(response_key, {}))
    return envelope


@pytest.mark.parametrize("account_id", [" ", "x" * 129])
def test_bootstrap_account_identity_bounds(account_id: str):
    with pytest.raises(ValidationError):
        BootstrapState(user=GameUser(id=account_id))


@pytest.mark.parametrize("account_id", ["x", "x" * 128])
def test_bootstrap_account_identity_boundary_values(account_id: str):
    assert BootstrapState(user=GameUser(id=account_id)).account_id == account_id


@pytest.fixture
async def game_client(settings):
    async with httpx.AsyncClient() as http:
        yield HttpGameClient(
            settings,
            session_token="test-session-token",
            http_client=http,
            timeout=0.1,
            request_spacing_seconds=0,
        )


def register_success(fake_game, method: str, path: str, response_key: str) -> None:
    fake_game.register(method, path, valid_envelope(response_key))


def assert_public_error_is_contained(error: BaseException, *markers: str) -> None:
    seen: set[int] = set()

    def inspect_value(value: Any) -> None:
        if value is None or isinstance(value, (bool, int, float)):
            return
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)

        assert not isinstance(value, (httpx.Request, httpx.Response, httpx.HTTPError))
        rendered = (str(value), repr(value))
        for marker in markers:
            assert marker not in rendered[0]
            assert marker not in rendered[1]

        if isinstance(value, BaseException):
            inspect_value(value.args)
            inspect_value(vars(value))
            inspect_value(value.__cause__)
            inspect_value(value.__context__)
        elif isinstance(value, Mapping):
            for key, nested in value.items():
                inspect_value(key)
                inspect_value(nested)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for nested in value:
                inspect_value(nested)

    inspect_value(error)


def test_registry_contains_exact_approved_operations_and_is_read_only():
    expected = {
        "login": ("POST", "/api/auth/login", True),
        "bootstrap": ("GET", "/api/client/bootstrap", False),
        "catalog": ("GET", "/api/client/catalog", False),
        "idle_summary": ("GET", "/api/client/idle-summary", False),
        "view_sections": ("POST", "/api/client/view-sections", False),
        "idle_collect": ("POST", "/api/battle/idle-collect", True),
        "boss_preview": ("POST", "/api/boss/preview", False),
        "boss_challenge": ("POST", "/api/boss/challenge", True),
        "boss_assist": ("POST", "/api/boss/assist", True),
        "profession_settle": ("POST", "/api/professions/settle", True),
        "profession_enqueue": ("POST", "/api/professions/queue/enqueue", True),
        "profession_supply_equip": (
            "POST",
            "/api/professions/supply/equip",
            True,
        ),
        "daily_claim": ("POST", "/api/daily/claim", True),
        "quest_claim": ("POST", "/api/quests/claim", True),
        "achievement_claim": ("POST", "/api/achievements/claim", True),
        "codex_claim": ("POST", "/api/codex/claim", True),
        "mail_claim": ("POST", "/api/mail/claim", True),
    }

    assert {
        name: (spec.method, spec.path, spec.mutation) for name, spec in REGISTRY.items()
    } == expected
    with pytest.raises(TypeError):
        REGISTRY["bootstrap"] = EndpointSpec("POST", "/api/unsafe", True)  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        REGISTRY["bootstrap"].path = "/api/unsafe"  # type: ignore[misc]


def test_public_surface_has_no_generic_or_unapproved_operations(game_client):
    forbidden = {
        "raw",
        "request",
        "mail_claim_all",
        "reward_claim",
        "claim_all",
        "market_claim",
    }
    assert forbidden.isdisjoint(REGISTRY)
    assert "/api/delete-all" not in {spec.path for spec in REGISTRY.values()}
    for name in forbidden:
        assert not hasattr(game_client, name)
    for name in REGISTRY:
        parameters = inspect.signature(getattr(game_client, name)).parameters
        assert "url" not in parameters
        assert "path" not in parameters


async def test_all_fixed_post_bodies_use_exact_api_aliases(fake_game, game_client):
    for method, path, response_key in (
        ("POST", "/api/client/view-sections", "view_sections"),
        ("POST", "/api/boss/preview", "boss_preview"),
        ("POST", "/api/boss/challenge", "boss_challenge"),
        ("POST", "/api/boss/assist", "boss_assist"),
        ("POST", "/api/professions/settle", "profession_settle"),
        ("POST", "/api/professions/queue/enqueue", "profession_enqueue"),
        ("POST", "/api/professions/supply/equip", "profession_supply_equip"),
        ("POST", "/api/daily/claim", "reward_claim"),
        ("POST", "/api/quests/claim", "reward_claim"),
        ("POST", "/api/achievements/claim", "reward_claim"),
        ("POST", "/api/codex/claim", "reward_claim"),
        ("POST", "/api/mail/claim", "reward_claim"),
    ):
        register_success(fake_game, method, path, response_key)

    preview = BossPreviewRequest(
        bossKey="boss-1",
        difficulty="hard",
        selectedSkillKeys=["skill-1", "skill-2"],
        buffKey="guard",
        affixKey="affix-1",
        targetSlot="weapon",
        useMaterialBoost=False,
    )
    challenge = BossChallengeRequest(
        bossKey="boss-2",
        difficulty="nightmare",
        selectedSkillKeys=["skill-3"],
        buffKey="focus",
        affixKey=None,
        targetSlot="armor",
        useMaterialBoost=True,
    )

    await game_client.view_sections(("bosses", "professions"), {"bosses": "etag-1"})
    await game_client.boss_preview(preview)
    await game_client.boss_challenge(challenge)
    await game_client.boss_assist("world-boss-1")
    await game_client.profession_settle()
    await game_client.profession_enqueue("forge", 3)
    await game_client.profession_supply_equip("potion", "guard-potion")
    await game_client.daily_claim(60)
    await game_client.quest_claim("quest-1")
    await game_client.achievement_claim("achievement-1")
    await game_client.codex_claim("codex-1")
    await game_client.mail_claim("mail-1")

    assert [(request.method, request.path, request.json_body) for request in fake_game.requests] == [
        (
            "POST",
            "/api/client/view-sections",
            {"sections": ["bosses", "professions"], "sectionEtags": {"bosses": "etag-1"}},
        ),
        (
            "POST",
            "/api/boss/preview",
            {
                "bossKey": "boss-1",
                "difficulty": "hard",
                "selectedSkillKeys": ["skill-1", "skill-2"],
                "buffKey": "guard",
                "affixKey": "affix-1",
                "targetSlot": "weapon",
                "useMaterialBoost": False,
            },
        ),
        (
            "POST",
            "/api/boss/challenge",
            {
                "bossKey": "boss-2",
                "difficulty": "nightmare",
                "selectedSkillKeys": ["skill-3"],
                "buffKey": "focus",
                "targetSlot": "armor",
                "useMaterialBoost": True,
            },
        ),
        ("POST", "/api/boss/assist", {"bossKey": "world-boss-1"}),
        ("POST", "/api/professions/settle", None),
        ("POST", "/api/professions/queue/enqueue", {"actionKey": "forge", "count": 3}),
        (
            "POST",
            "/api/professions/supply/equip",
            {"supplyType": "potion", "itemKey": "guard-potion"},
        ),
        ("POST", "/api/daily/claim", {"point": 60}),
        ("POST", "/api/quests/claim", {"questKey": "quest-1"}),
        (
            "POST",
            "/api/achievements/claim",
            {"achievementKey": "achievement-1"},
        ),
        ("POST", "/api/codex/claim", {"rewardKey": "codex-1"}),
        ("POST", "/api/mail/claim", {"mailId": "mail-1"}),
    ]
    assert all(
        request.headers["authorization"] == "[REDACTED]"
        for request in fake_game.requests
    )


async def test_login_omits_bearer_and_fake_redacts_password(fake_game, settings):
    register_success(fake_game, "POST", "/api/auth/login", "login")
    async with httpx.AsyncClient() as http:
        client = HttpGameClient(
            settings,
            session_token="old-token",
            http_client=http,
            request_spacing_seconds=0,
        )
        result = await client.login("user", "login-password-marker")

    assert result.session_token == "new-session-token"
    request = fake_game.requests[-1]
    assert "authorization" not in request.headers
    assert request.json_body == {"username": "user", "password": "[REDACTED]"}
    assert len(fake_game.requests) == 1


async def test_fake_recursively_redacts_future_body_and_header_credentials(fake_game):
    fake_game.register("POST", "/api/auth/login", {"ok": True})
    async with httpx.AsyncClient() as http:
        response = await http.post(
            fake_game.url + "/api/auth/login",
            headers={
                "Authorization": "header-auth-marker",
                "Cookie": "header-cookie-marker",
                "X-Api-Key": "header-api-key-marker",
                "X-Auth": "header-auth-like-marker",
                "X-Trace": "trace-safe",
            },
            json={
                "username": "user",
                "nested": {
                    "password": "nested-password-marker",
                    "accessToken": "nested-token-marker",
                    "client_secret": "nested-secret-marker",
                    "cookieJar": ["nested-cookie-marker"],
                    "auth": "nested-auth-like-marker",
                    "safe": "safe-value",
                },
            },
        )
    assert response.status_code == 200

    request = fake_game.requests[-1]
    assert request.headers["authorization"] == "[REDACTED]"
    assert request.headers["cookie"] == "[REDACTED]"
    assert request.headers["x-api-key"] == "[REDACTED]"
    assert request.headers["x-auth"] == "[REDACTED]"
    assert request.headers["x-trace"] == "trace-safe"
    assert request.json_body == {
        "username": "user",
        "nested": {
            "password": "[REDACTED]",
            "accessToken": "[REDACTED]",
            "client_secret": "[REDACTED]",
            "cookieJar": "[REDACTED]",
            "auth": "[REDACTED]",
            "safe": "safe-value",
        },
    }


@pytest.mark.parametrize(
    ("method", "path", "response_key", "call"),
    [
        ("POST", "/api/auth/login", "login", lambda client: client.login("u", "p")),
        ("GET", "/api/client/bootstrap", "bootstrap", lambda client: client.bootstrap()),
        ("GET", "/api/client/idle-summary", "idle_summary", lambda client: client.idle_summary()),
        (
            "POST",
            "/api/client/view-sections",
            "view_sections",
            lambda client: client.view_sections(("bosses",)),
        ),
        ("POST", "/api/battle/idle-collect", "idle_collect", lambda client: client.idle_collect()),
        (
            "POST",
            "/api/boss/preview",
            "boss_preview",
            lambda client: client.boss_preview(
                BossPreviewRequest(
                    bossKey="boss",
                    difficulty="normal",
                    selectedSkillKeys=[],
                    buffKey="none",
                    affixKey=None,
                    targetSlot="weapon",
                    useMaterialBoost=False,
                )
            ),
        ),
        (
            "POST",
            "/api/boss/challenge",
            "boss_challenge",
            lambda client: client.boss_challenge(
                BossChallengeRequest(
                    bossKey="boss",
                    difficulty="normal",
                    selectedSkillKeys=[],
                    buffKey="none",
                    affixKey=None,
                    targetSlot="weapon",
                    useMaterialBoost=False,
                )
            ),
        ),
        ("POST", "/api/boss/assist", "boss_assist", lambda client: client.boss_assist("boss")),
        (
            "POST",
            "/api/professions/settle",
            "profession_settle",
            lambda client: client.profession_settle(),
        ),
        (
            "POST",
            "/api/professions/queue/enqueue",
            "profession_enqueue",
            lambda client: client.profession_enqueue("forge", 1),
        ),
        (
            "POST",
            "/api/professions/supply/equip",
            "profession_supply_equip",
            lambda client: client.profession_supply_equip("potion", "item"),
        ),
        ("POST", "/api/quests/claim", "reward_claim", lambda client: client.quest_claim("quest")),
    ],
)
async def test_missing_required_response_core_becomes_schema_mismatch(
    fake_game, game_client, method, path, response_key, call
):
    fake_game.register(method, path, {"ok": True, "data": {}})

    with pytest.raises(GameSchemaMismatch) as captured:
        await call(game_client)

    assert captured.value.metadata == {"status_code": 200}
    assert_public_error_is_contained(captured.value)


@pytest.mark.parametrize(
    ("method", "path", "call", "invalid_data"),
    [
        (
            "POST",
            "/api/auth/login",
            lambda client: client.login("user", "password"),
            {"sessionToken": 123},
        ),
        (
            "POST",
            "/api/auth/login",
            lambda client: client.login("user", "password"),
            {"sessionToken": ""},
        ),
        (
            "POST",
            "/api/boss/preview",
            lambda client: client.boss_preview(
                BossPreviewRequest(
                    bossKey="boss",
                    difficulty="normal",
                    selectedSkillKeys=[],
                    buffKey="none",
                    affixKey=None,
                    targetSlot="weapon",
                    useMaterialBoost=False,
                )
            ),
            {"predictedWin": "yes", "chance": "certain"},
        ),
        (
            "POST",
            "/api/boss/assist",
            lambda client: client.boss_assist("boss"),
            {"myAttemptCount": "one", "remainingAttemptCount": -1},
        ),
    ],
)
async def test_wrong_typed_response_core_becomes_schema_mismatch(
    fake_game, game_client, method, path, call, invalid_data
):
    fake_game.register(method, path, {"data": invalid_data})

    with pytest.raises(GameSchemaMismatch) as captured:
        await call(game_client)

    assert captured.value.metadata == {"status_code": 200}
    assert_public_error_is_contained(captured.value)


@pytest.mark.parametrize(
    "invalid_data",
    [
        {"predictedWin": True, "chance": 85.0},
        {
            "predictedWin": True,
            "chance": 85.0,
            "playerHpRemainingPercent": "75",
            "bossHpRemainingPercent": 0.0,
        },
        {
            "predictedWin": False,
            "chance": -1.0,
            "playerHpRemainingPercent": 0.0,
            "bossHpRemainingPercent": 100.0,
        },
        {
            "predictedWin": False,
            "chance": 20.0,
            "playerHpRemainingPercent": 0.0,
            "bossHpRemainingPercent": 101.0,
        },
    ],
)
async def test_boss_preview_rejects_incomplete_or_invalid_ranking_signals(
    fake_game, game_client, invalid_data
):
    fake_game.register("POST", "/api/boss/preview", {"data": invalid_data})

    with pytest.raises(GameSchemaMismatch):
        await game_client.boss_preview(
            BossPreviewRequest(
                bossKey="boss",
                difficulty="hard",
                selectedSkillKeys=[],
                buffKey="none",
                affixKey=None,
                targetSlot="weapon",
                useMaterialBoost=False,
            )
        )


async def test_view_sections_validates_public_top_level_boss_shape(fake_game, game_client):
    fake_game.register(
        "POST",
        "/api/client/view-sections",
        {
            "data": {
                **VALID_RESPONSES["view_sections"],
                "professions": {"queue": []},
                "unknownFutureSection": {"kept": True},
            }
        },
    )

    result = await game_client.view_sections(("bosses", "professions"))

    assert result.section_etags == {"bosses": "bosses-etag"}
    assert result.bosses is not None
    personal, map_boss, world = result.bosses
    assert personal.key == "personal-boss-1"
    assert personal.personal_attempt_pool is not None
    assert personal.personal_attempt_pool.free_remaining == 2
    assert personal.attempts is None
    assert personal.refresh_text is None
    assert map_boss.key == "map-boss-1"
    assert map_boss.attempts == 2
    assert map_boss.personal_attempt_pool is None
    assert world.key == "world-boss-1"
    assert world.blocked_reason == "not_active"
    assert world.refresh_text == "opens at 20:00"
    assert world.difficulty_options[0].player_hp_remaining_percent == 61.5
    assert result.model_extra["unknownFutureSection"] == {"kept": True}


async def test_view_sections_allows_non_boss_request_without_bosses(fake_game, game_client):
    fake_game.register(
        "POST",
        "/api/client/view-sections",
        {
            "data": {
                "sectionEtags": {"professions": "professions-etag"},
                "professions": {"queue": []},
            }
        },
    )

    result = await game_client.view_sections(("professions",))

    assert result.section_etags == {"professions": "professions-etag"}
    assert result.bosses is None
    assert result.model_extra["professions"] == {"queue": []}


async def test_view_sections_requires_bosses_when_bosses_were_requested(
    fake_game, game_client
):
    fake_game.register(
        "POST",
        "/api/client/view-sections",
        {"data": {"sectionEtags": {"bosses": "bosses-etag"}}},
    )

    with pytest.raises(GameSchemaMismatch):
        await game_client.view_sections(("bosses",))


@pytest.mark.parametrize(
    ("base_boss", "boss_override"),
    [
        (VALID_MAP_BOSS, {"attempts": "two"}),
        (VALID_MAP_BOSS, {"blockedReason": 7}),
        (
            VALID_MAP_BOSS,
            {"difficultyOptions": [{**VALID_BOSS_DIFFICULTY, "chance": "likely"}]},
        ),
        (
            VALID_MAP_BOSS,
            {
                "difficultyOptions": [
                    {**VALID_BOSS_DIFFICULTY, "playerHpRemainingPercent": "61.5"}
                ]
            },
        ),
        (VALID_WORLD_BOSS, {"refreshText": 7}),
        (
            VALID_PERSONAL_BOSS,
            {
                "personalAttemptPool": {
                    **VALID_PERSONAL_BOSS["personalAttemptPool"],
                    "freeRemaining": "two",
                },
            },
        ),
    ],
)
async def test_view_sections_rejects_wrong_known_boss_field_types(
    fake_game, game_client, base_boss, boss_override
):
    fake_game.register(
        "POST",
        "/api/client/view-sections",
        {
            "data": {
                "sectionEtags": {"bosses": "bosses-etag"},
                "bosses": [{**base_boss, **boss_override}],
            }
        },
    )

    with pytest.raises(GameSchemaMismatch):
        await game_client.view_sections(("bosses",))


async def test_get_and_post_reads_each_retry_exactly_three_timeouts(fake_game, game_client):
    register_success(fake_game, "GET", "/api/client/catalog", "catalog")
    fake_game.timeout("GET", "/api/client/catalog", count=3)
    with pytest.raises(GameUnavailable):
        await game_client.catalog()

    register_success(fake_game, "POST", "/api/boss/preview", "boss_preview")
    fake_game.timeout("POST", "/api/boss/preview", count=3)
    with pytest.raises(GameUnavailable):
        await game_client.boss_preview(
            BossPreviewRequest(
                bossKey="boss",
                difficulty="normal",
                selectedSkillKeys=[],
                buffKey="none",
                affixKey=None,
                targetSlot="weapon",
                useMaterialBoost=False,
            )
        )

    assert [(request.method, request.path) for request in fake_game.requests] == [
        ("GET", "/api/client/catalog"),
        ("GET", "/api/client/catalog"),
        ("GET", "/api/client/catalog"),
        ("POST", "/api/boss/preview"),
        ("POST", "/api/boss/preview"),
        ("POST", "/api/boss/preview"),
    ]


@pytest.mark.parametrize(
    ("path", "call"),
    [
        ("/api/auth/login", lambda client: client.login("user", "password")),
        ("/api/battle/idle-collect", lambda client: client.idle_collect()),
    ],
)
async def test_login_and_mutation_timeout_once_and_are_ambiguous(
    fake_game, game_client, path, call
):
    response_key = "login" if path.endswith("login") else "idle_collect"
    register_success(fake_game, "POST", path, response_key)
    fake_game.timeout("POST", path)

    with pytest.raises(AmbiguousMutation):
        await call(game_client)

    assert [(request.method, request.path) for request in fake_game.requests] == [("POST", path)]


@pytest.mark.parametrize(
    ("status_code", "body", "error"),
    [
        (401, {"error": {"code": "session_rejected"}}, SessionRejected),
        (403, {"error": {"code": "session_rejected"}}, SessionRejected),
        (426, {"error": {"code": "upgrade_required"}}, ContractChanged),
        (400, {"error": {"code": "inventory_full"}}, InventoryFull),
        (400, {"error": {"code": "insufficient_resource"}}, InsufficientResource),
        (409, {"error": {"code": "conflict"}}, GameConflict),
        (429, {"error": {"code": "rate_limited"}}, GameRateLimited),
    ],
)
async def test_stable_envelope_and_status_mappings(
    fake_game, game_client, status_code, body, error
):
    fake_game.register("GET", "/api/client/catalog", body, status_code=status_code)

    with pytest.raises(error) as captured:
        await game_client.catalog()

    assert_public_error_is_contained(captured.value)


async def test_unmapped_status_is_typed_with_allowlisted_metadata(fake_game, game_client):
    error_code_marker = "pgm_" + "s" * 48
    fake_game.register(
        "GET",
        "/api/client/catalog",
        {"error": {"code": error_code_marker}, "secret": "raw-body-marker"},
        status_code=400,
        headers={
            "Location": "https://example.invalid/?token=location-header-marker",
            "X-Api-Key": "api-key-header-marker",
        },
    )

    with pytest.raises(GameError) as captured:
        await game_client.catalog()

    assert type(captured.value).__name__ == "GameHttpError"
    assert isinstance(captured.value, GameHttpError)
    assert captured.value.metadata == {"status_code": 400}
    assert_public_error_is_contained(
        captured.value,
        error_code_marker,
        "raw-body-marker",
        "location-header-marker",
        "api-key-header-marker",
        "test-session-token",
    )


async def test_unknown_conflict_code_is_not_retained(fake_game, game_client):
    error_code_marker = "pgm_" + "c" * 48
    fake_game.register(
        "GET",
        "/api/client/catalog",
        {"error": {"code": error_code_marker}},
        status_code=409,
    )

    with pytest.raises(GameConflict) as captured:
        await game_client.catalog()

    assert captured.value.code is None
    assert_public_error_is_contained(captured.value, error_code_marker)


async def test_read_5xx_is_typed_and_mutation_5xx_is_ambiguous(fake_game, game_client):
    fake_game.register("GET", "/api/client/catalog", {"error": {}}, status_code=503)
    with pytest.raises(GameUnavailable) as read_error:
        await game_client.catalog()
    assert_public_error_is_contained(read_error.value)

    fake_game.register("POST", "/api/battle/idle-collect", {"error": {}}, status_code=503)
    with pytest.raises(AmbiguousMutation) as mutation_error:
        await game_client.idle_collect()
    assert_public_error_is_contained(mutation_error.value)
    assert [request.path for request in fake_game.requests] == [
        "/api/client/catalog",
        "/api/battle/idle-collect",
    ]


async def test_mutation_5xx_is_ambiguous_regardless_of_error_envelope(
    fake_game, game_client
):
    fake_game.register(
        "POST",
        "/api/battle/idle-collect",
        {"error": {"code": "inventory_full"}},
        status_code=503,
    )

    with pytest.raises(AmbiguousMutation):
        await game_client.idle_collect()

    assert [request.path for request in fake_game.requests] == [
        "/api/battle/idle-collect"
    ]


async def test_read_transport_failure_contains_bearer_and_httpx_objects(settings):
    bearer_marker = "bearer-transport-marker"

    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("transport-error-marker", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        client = HttpGameClient(
            settings,
            session_token=bearer_marker,
            http_client=http,
            request_spacing_seconds=0,
            sleeper=lambda _: _noop(),
        )
        with pytest.raises(GameUnavailable) as captured:
            await client.catalog()

    assert_public_error_is_contained(
        captured.value, bearer_marker, "transport-error-marker"
    )


async def test_login_transport_failure_contains_password_and_httpx_objects(settings):
    password_marker = "login-password-transport-marker"

    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("login-timeout-marker", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        client = HttpGameClient(
            settings,
            http_client=http,
            request_spacing_seconds=0,
        )
        with pytest.raises(AmbiguousMutation) as captured:
            await client.login("user", password_marker)

    assert_public_error_is_contained(
        captured.value, password_marker, "login-timeout-marker"
    )


async def test_schema_error_contains_no_raw_body_headers_or_httpx_objects(fake_game, game_client):
    fake_game.register(
        "GET",
        "/api/client/idle-summary",
        {"ok": True, "data": {"validSeconds": {"raw": "schema-body-marker"}}},
        headers={
            "Location": "https://example.invalid/?token=schema-location-marker",
            "X-Api-Key": "schema-api-key-marker",
        },
    )

    with pytest.raises(GameSchemaMismatch) as captured:
        await game_client.idle_summary()

    assert captured.value.metadata == {"status_code": 200}
    assert_public_error_is_contained(
        captured.value,
        "schema-body-marker",
        "schema-location-marker",
        "schema-api-key-marker",
        "test-session-token",
    )


async def test_request_spacing_is_deterministic_and_per_client(settings):
    now = 100.0
    delays: list[float] = []

    def monotonic() -> float:
        return now

    async def sleep(delay: float) -> None:
        nonlocal now
        delays.append(delay)
        now += delay

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=valid_envelope("bootstrap"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as first_http:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as second_http:
            first = HttpGameClient(
                settings,
                http_client=first_http,
                request_spacing_seconds=0.5,
                monotonic=monotonic,
                sleeper=sleep,
            )
            second = HttpGameClient(
                settings,
                http_client=second_http,
                request_spacing_seconds=0.5,
                monotonic=monotonic,
                sleeper=sleep,
            )
            await first.bootstrap()
            await first.bootstrap()
            await second.bootstrap()

    assert delays == [0.5]


async def _noop() -> None:
    return None
