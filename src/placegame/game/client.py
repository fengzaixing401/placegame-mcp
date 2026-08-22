import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Final, Literal, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from placegame.config import Settings
from placegame.errors import (
    AmbiguousMutation,
    ClientVersionRejected,
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

from .registry import OperationName, REGISTRY
from .schemas import (
    AchievementClaimRequest,
    BossAssistRequest,
    BossAssistResult,
    BossChallengeRequest,
    BossChallengeResult,
    BossState,
    BossPreview,
    BossPreviewRequest,
    BootstrapState,
    Catalog,
    CodexClaimRequest,
    DailyClaimRequest,
    EquipmentIdRequest,
    EquipmentIdsRequest,
    IdleCollectResult,
    IdleSummary,
    LoginRequest,
    LoginResult,
    MailClaimRequest,
    PassthroughResult,
    ProfessionEnqueueRequest,
    ProfessionQueueResult,
    ProfessionSettleResult,
    ProfessionSupplyEquipRequest,
    ProfessionSupplyResult,
    ProfessionState,
    QuestClaimRequest,
    RewardClaimResult,
    RewardState,
    ViewSections,
    ViewSectionsRequest,
    WorldBossState,
)


T = TypeVar("T", bound=BaseModel)
ViewSection = str
Sleeper = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


_STABLE_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "conflict",
        "insufficient_resource",
        "inventory_full",
    }
)

# Same shape the official CLI accepts for its own update manifest.
CLIENT_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]{1,32})?", re.ASCII
)


class GameClientVersion:
    """The client version the game currently demands, shared across accounts.

    The game gates every endpoint on `x-placegame-client-version` and answers 426
    with the version it wants, so a stale seed costs one rejected request and then
    corrects itself. No local pin can stay right across a game release.
    """

    def __init__(self, seed: str) -> None:
        if CLIENT_VERSION_PATTERN.fullmatch(seed) is None:
            raise ValueError("client version must look like a semantic version")
        self._value = seed

    @property
    def value(self) -> str:
        return self._value

    def adopt(self, candidate: str) -> bool:
        """Take the version the game asked for. False when it is not news."""

        if CLIENT_VERSION_PATTERN.fullmatch(candidate) is None:
            return False
        if candidate == self._value:
            return False
        self._value = candidate
        return True


class GameApi(Protocol):
    async def login(self, username: str, password: str) -> LoginResult: ...
    async def bootstrap(self) -> BootstrapState: ...
    async def catalog(self) -> Catalog: ...
    async def idle_summary(self) -> IdleSummary: ...
    async def view_sections(
        self,
        sections: tuple[ViewSection, ...],
        section_etags: dict[ViewSection, str] | None = None,
    ) -> ViewSections: ...
    async def boss_state(self) -> BossState: ...
    async def world_boss_state(self) -> WorldBossState: ...
    async def profession_state(self) -> ProfessionState: ...
    async def reward_state(self) -> RewardState: ...
    async def idle_collect(self) -> IdleCollectResult: ...
    async def equipment_list(self) -> PassthroughResult: ...
    async def equipment_decompose_preview(
        self, equipment_ids: Sequence[str]
    ) -> PassthroughResult: ...
    async def equipment_enhance_preview(
        self, equipment_id: str
    ) -> PassthroughResult: ...
    async def equipment_quality_upgrade_preview(
        self, equipment_id: str
    ) -> PassthroughResult: ...
    async def boss_preview(self, request: BossPreviewRequest) -> BossPreview: ...
    async def boss_challenge(
        self, request: BossChallengeRequest
    ) -> BossChallengeResult: ...
    async def boss_assist(self, boss_key: str) -> BossAssistResult: ...
    async def profession_settle(self) -> ProfessionSettleResult: ...
    async def profession_enqueue(
        self, action_key: str, count: int
    ) -> ProfessionQueueResult: ...
    async def profession_supply_equip(
        self, supply_type: str, item_key: str
    ) -> ProfessionSupplyResult: ...
    async def daily_claim(self, point: int) -> RewardClaimResult: ...
    async def quest_claim(self, quest_key: str) -> RewardClaimResult: ...
    async def achievement_claim(self, achievement_key: str) -> RewardClaimResult: ...
    async def codex_claim(self, reward_key: str) -> RewardClaimResult: ...
    async def mail_claim(self, mail_id: str) -> RewardClaimResult: ...


class AccountRateLimiter:
    def __init__(
        self,
        spacing_seconds: float,
        *,
        monotonic: Clock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._spacing_seconds = spacing_seconds
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._lock = asyncio.Lock()
        self._next_allowed_at = 0.0

    async def __aenter__(self) -> None:
        async with self._lock:
            delay = self._next_allowed_at - self._monotonic()
            if delay > 0:
                await self._sleeper(delay)
            self._next_allowed_at = self._monotonic() + self._spacing_seconds

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


FailureKind = Literal[
    "ambiguous",
    "unavailable",
    "session_rejected",
    "contract_changed",
    "client_version_rejected",
    "inventory_full",
    "insufficient_resource",
    "conflict",
    "rate_limited",
    "schema_mismatch",
    "http_error",
]


@dataclass(frozen=True)
class _Failure:
    kind: FailureKind
    metadata: dict[str, object] | None = None
    code: str | None = None
    retry_after: float | None = None
    required_version: str | None = None


@dataclass(frozen=True)
class _TransportFailure:
    pass


@dataclass(frozen=True)
class _ConnectFailure:
    """The connection was never established, so the request never reached the game.

    A mutation is only ambiguous once it may have been acted on. Failing to connect
    carries no such doubt, so these are safe to retry even for a mutation — and on
    a flaky link they are the common case.
    """


def _safe_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    candidate = error.get("code") if isinstance(error, dict) else payload.get("code")
    if not isinstance(candidate, str) or candidate not in _STABLE_ERROR_CODES:
        return None
    return candidate


def _required_client_version(response: httpx.Response) -> str | None:
    """Read the version a 426 asks us to declare, if it is a plausible one."""

    try:
        payload = response.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    candidate = data.get("minimumClientVersion") if isinstance(data, dict) else None
    if not isinstance(candidate, str) or CLIENT_VERSION_PATTERN.fullmatch(candidate) is None:
        return None
    return candidate


def _parse_retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


def _safe_metadata(status_code: int, error_code: str | None = None) -> dict[str, object]:
    metadata: dict[str, object] = {"status_code": status_code}
    if error_code is not None:
        metadata["error_code"] = error_code
    return metadata


def _payload_for(model: type[T], envelope: dict[str, object]) -> object:
    """Pick the object a response model validates against.

    Mutating endpoints answer with `data = {"result": ..., "statePatch": ...}` and
    only the result is the operation's own payload.
    """

    if getattr(model, "reads_envelope", False):
        return envelope
    data = envelope["data"]
    if isinstance(data, dict) and "result" in data and "statePatch" in data:
        return data["result"]
    return data


def _interpret_response(response: httpx.Response, model: type[T]) -> T | _Failure:
    status_code = response.status_code
    error_code = _safe_error_code(response)

    if not 200 <= status_code < 300:
        metadata = _safe_metadata(status_code, error_code)
        if status_code in {401, 403}:
            return _Failure("session_rejected")
        if status_code == 426:
            return _Failure(
                "client_version_rejected",
                metadata=metadata,
                required_version=_required_client_version(response),
            )
        if status_code >= 500:
            return _Failure("unavailable")
        if error_code == "inventory_full":
            return _Failure("inventory_full")
        if error_code == "insufficient_resource":
            return _Failure("insufficient_resource", metadata=metadata)
        if status_code == 409:
            return _Failure("conflict", code=error_code)
        if status_code == 429:
            return _Failure(
                "rate_limited", retry_after=_parse_retry_after(response.headers)
            )
        return _Failure("http_error", metadata=metadata)

    try:
        envelope = response.json()
        if not isinstance(envelope, dict):
            return _Failure(
                "schema_mismatch", metadata=_safe_metadata(status_code)
            )
        # The game reports business failures as 200 with {"ok": false, "error": ...},
        # so a 2xx status alone does not mean the operation succeeded. Treating it as
        # a schema mismatch would misreport an ordinary refusal as a broken contract.
        if envelope.get("ok") is False:
            return _Failure("conflict", code=error_code)
        return model.model_validate(_payload_for(model, envelope))
    except (KeyError, TypeError, ValueError, ValidationError):
        return _Failure("schema_mismatch", metadata=_safe_metadata(status_code))


def _public_error(operation: OperationName, failure: _Failure) -> GameError:
    if failure.kind == "ambiguous":
        return AmbiguousMutation(operation)
    if failure.kind == "unavailable":
        return GameUnavailable(operation)
    if failure.kind == "session_rejected":
        return SessionRejected()
    if failure.kind == "contract_changed":
        return ContractChanged()
    if failure.kind == "client_version_rejected":
        return ClientVersionRejected()
    if failure.kind == "inventory_full":
        return InventoryFull()
    if failure.kind == "insufficient_resource":
        return InsufficientResource(failure.metadata)
    if failure.kind == "conflict":
        return GameConflict(failure.code)
    if failure.kind == "rate_limited":
        return GameRateLimited(failure.retry_after)
    if failure.kind == "schema_mismatch":
        return GameSchemaMismatch(operation, failure.metadata or {})
    return GameHttpError(operation, failure.metadata or {})


class HttpGameClient:
    def __init__(
        self,
        settings: Settings,
        *,
        session_token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        client_version: GameClientVersion | None = None,
        timeout: float = 15.0,
        request_spacing_seconds: float = 0.1,
        monotonic: Clock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
        jitter: Clock = random.random,
    ) -> None:
        if request_spacing_seconds < 0:
            raise ValueError("request spacing cannot be negative")
        self._base_url = settings.game_base_url.rstrip("/")
        self._session_token = session_token
        self._http = http_client or httpx.AsyncClient()
        self._owns_http_client = http_client is None
        self._client_version = client_version or GameClientVersion(
            settings.game_client_version
        )
        self._timeout = timeout
        self._sleeper = sleeper
        self._jitter = jitter
        self._rate_limiter = AccountRateLimiter(
            request_spacing_seconds, monotonic=monotonic, sleeper=sleeper
        )

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def _send_once(
        self,
        operation: OperationName,
        payload: dict[str, object] | None,
    ) -> httpx.Response | _TransportFailure:
        spec = REGISTRY[operation]
        headers = {
            "Accept": "application/json",
            # Mirrors the official CLI. `omit` keeps the server from wrapping the
            # payload in {"result": ..., "statePatch": ...}.
            "x-placegame-client-platform": "cli",
            "x-placegame-client-version": self._client_version.value,
            "x-placegame-response-state": "omit",
        }
        if operation != "login" and self._session_token is not None:
            headers["Authorization"] = f"Bearer {self._session_token}"
        try:
            async with self._rate_limiter:
                if payload is None:
                    return await self._http.request(
                        spec.method,
                        self._base_url + spec.path,
                        headers=headers,
                        timeout=self._timeout,
                    )
                return await self._http.request(
                    spec.method,
                    self._base_url + spec.path,
                    headers=headers,
                    timeout=self._timeout,
                    json=payload,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return _ConnectFailure()
        except httpx.HTTPError:
            return _TransportFailure()

    async def _request_outcome(
        self,
        operation: OperationName,
        response: type[T],
        body: BaseModel | None,
    ) -> T | _Failure:
        outcome = await self._attempt(operation, response, body)
        if (
            isinstance(outcome, _Failure)
            and outcome.kind == "client_version_rejected"
            and outcome.required_version is not None
            and self._client_version.adopt(outcome.required_version)
        ):
            # A 426 is refused before the game does any work, so retrying is safe
            # even for a mutation. The version is now the one the game named.
            outcome = await self._attempt(operation, response, body)
        return outcome

    async def _attempt(
        self,
        operation: OperationName,
        response: type[T],
        body: BaseModel | None,
    ) -> T | _Failure:
        spec = REGISTRY[operation]
        payload = None
        if body is not None:
            payload = body.model_dump(mode="json", by_alias=True, exclude_none=True)
        # A mutation may not be replayed once it could have been acted on, but a
        # connection that never opened is retryable whatever the operation is.
        attempts = 3

        for attempt in range(attempts):
            result = await self._send_once(operation, payload)
            if isinstance(result, (_TransportFailure, _ConnectFailure)):
                connect_only = isinstance(result, _ConnectFailure)
                if spec.mutation and not connect_only:
                    return _Failure("ambiguous")
                if attempt == attempts - 1:
                    return _Failure("ambiguous" if spec.mutation else "unavailable")
                await self._sleeper((2**attempt) * 0.1 + self._jitter() * 0.1)
                continue

            outcome = _interpret_response(result, response)
            if isinstance(outcome, _Failure) and outcome.kind == "unavailable":
                if spec.mutation:
                    return _Failure("ambiguous")
            return outcome

        raise AssertionError("unreachable")

    async def _request(
        self,
        operation: OperationName,
        response: type[T],
        body: BaseModel | None = None,
    ) -> T:
        outcome = await self._request_outcome(operation, response, body)
        if isinstance(outcome, _Failure):
            raise _public_error(operation, outcome)
        return outcome

    async def login(self, username: str, password: str) -> LoginResult:
        return await self._request(
            "login", LoginResult, LoginRequest(username=username, password=password)
        )

    async def bootstrap(self) -> BootstrapState:
        return await self._request("bootstrap", BootstrapState)

    async def catalog(self) -> Catalog:
        return await self._request("catalog", Catalog)

    async def idle_summary(self) -> IdleSummary:
        return await self._request("idle_summary", IdleSummary)

    async def view_sections(
        self,
        sections: tuple[ViewSection, ...],
        section_etags: dict[ViewSection, str] | None = None,
    ) -> ViewSections:
        result = await self._request(
            "view_sections",
            ViewSections,
            ViewSectionsRequest(sections=sections, sectionEtags=section_etags),
        )
        if "bosses" in sections and result.bosses is None:
            raise GameSchemaMismatch("view_sections", {"status_code": 200})
        return result

    async def boss_state(self) -> BossState:
        state = (await self.view_sections(("bosses",))).boss_state
        if state is None:
            raise GameSchemaMismatch("view_sections", {"status_code": 200})
        return state

    async def world_boss_state(self) -> WorldBossState:
        state = (await self.view_sections(("bosses",))).world_boss_state
        if state is None:
            raise GameSchemaMismatch("view_sections", {"status_code": 200})
        return state

    async def profession_state(self) -> ProfessionState:
        state = (await self.view_sections(("professions",))).profession_state
        if state is None:
            raise GameSchemaMismatch("view_sections", {"status_code": 200})
        return state

    async def reward_state(self) -> RewardState:
        state = (await self.view_sections(("rewards",))).reward_state
        if state is None:
            raise GameSchemaMismatch("view_sections", {"status_code": 200})
        return state

    async def idle_collect(self) -> IdleCollectResult:
        return await self._request("idle_collect", IdleCollectResult)

    async def equipment_list(self) -> PassthroughResult:
        return await self._request("equipment_list", PassthroughResult)

    async def equipment_decompose_preview(
        self, equipment_ids: Sequence[str]
    ) -> PassthroughResult:
        return await self._request(
            "equipment_decompose_preview",
            PassthroughResult,
            EquipmentIdsRequest(equipmentIds=list(equipment_ids)),
        )

    async def equipment_enhance_preview(self, equipment_id: str) -> PassthroughResult:
        return await self._request(
            "equipment_enhance_preview",
            PassthroughResult,
            EquipmentIdRequest(equipmentId=equipment_id),
        )

    async def equipment_quality_upgrade_preview(
        self, equipment_id: str
    ) -> PassthroughResult:
        return await self._request(
            "equipment_quality_upgrade_preview",
            PassthroughResult,
            EquipmentIdRequest(equipmentId=equipment_id),
        )

    async def boss_preview(self, request: BossPreviewRequest) -> BossPreview:
        return await self._request("boss_preview", BossPreview, request)

    async def boss_challenge(
        self, request: BossChallengeRequest
    ) -> BossChallengeResult:
        return await self._request("boss_challenge", BossChallengeResult, request)

    async def boss_assist(self, boss_key: str) -> BossAssistResult:
        return await self._request(
            "boss_assist", BossAssistResult, BossAssistRequest(bossKey=boss_key)
        )

    async def profession_settle(self) -> ProfessionSettleResult:
        return await self._request("profession_settle", ProfessionSettleResult)

    async def profession_enqueue(
        self, action_key: str, count: int
    ) -> ProfessionQueueResult:
        return await self._request(
            "profession_enqueue",
            ProfessionQueueResult,
            ProfessionEnqueueRequest(actionKey=action_key, count=count),
        )

    async def profession_supply_equip(
        self, supply_type: str, item_key: str
    ) -> ProfessionSupplyResult:
        return await self._request(
            "profession_supply_equip",
            ProfessionSupplyResult,
            ProfessionSupplyEquipRequest(supplyType=supply_type, itemKey=item_key),
        )

    async def daily_claim(self, point: int) -> RewardClaimResult:
        return await self._request(
            "daily_claim", RewardClaimResult, DailyClaimRequest(point=point)
        )

    async def quest_claim(self, quest_key: str) -> RewardClaimResult:
        return await self._request(
            "quest_claim", RewardClaimResult, QuestClaimRequest(questKey=quest_key)
        )

    async def achievement_claim(self, achievement_key: str) -> RewardClaimResult:
        return await self._request(
            "achievement_claim",
            RewardClaimResult,
            AchievementClaimRequest(achievementKey=achievement_key),
        )

    async def codex_claim(self, reward_key: str) -> RewardClaimResult:
        return await self._request(
            "codex_claim", RewardClaimResult, CodexClaimRequest(rewardKey=reward_key)
        )

    async def mail_claim(self, mail_id: str) -> RewardClaimResult:
        return await self._request(
            "mail_claim", RewardClaimResult, MailClaimRequest(mailId=mail_id)
        )
