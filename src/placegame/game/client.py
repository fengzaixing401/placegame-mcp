import asyncio
import random
import time
from email.utils import parsedate_to_datetime
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from placegame.config import Settings
from placegame.errors import (
    AmbiguousMutation,
    ContractChanged,
    GameConflict,
    GameRateLimited,
    GameSchemaMismatch,
    GameUnavailable,
    InsufficientResource,
    InventoryFull,
    SessionRejected,
)
from placegame.security.redaction import redact

from .registry import OperationName, REGISTRY
from .schemas import (
    AchievementClaimRequest,
    BossAssistRequest,
    BossAssistResult,
    BossChallengeRequest,
    BossChallengeResult,
    BossPreview,
    BossPreviewRequest,
    BootstrapState,
    Catalog,
    CodexClaimRequest,
    DailyClaimRequest,
    IdleCollectResult,
    IdleSummary,
    LoginRequest,
    LoginResult,
    MailClaimRequest,
    ProfessionEnqueueRequest,
    ProfessionQueueResult,
    ProfessionSettleResult,
    ProfessionSupplyEquipRequest,
    ProfessionSupplyResult,
    QuestClaimRequest,
    RewardClaimResult,
    ViewSections,
    ViewSectionsRequest,
)


T = TypeVar("T", bound=BaseModel)
ViewSection = str


class GameApi(Protocol):
    async def login(self, username: str, password: str) -> LoginResult: ...
    async def bootstrap(self) -> BootstrapState: ...
    async def catalog(self) -> Catalog: ...
    async def idle_summary(self) -> IdleSummary: ...
    async def view_sections(
        self, sections: tuple[ViewSection, ...], section_etags: dict[ViewSection, str] | None = None
    ) -> ViewSections: ...
    async def idle_collect(self) -> IdleCollectResult: ...
    async def boss_preview(self, request: BossPreviewRequest) -> BossPreview: ...
    async def boss_challenge(self, request: BossChallengeRequest) -> BossChallengeResult: ...
    async def boss_assist(self, boss_key: str) -> BossAssistResult: ...
    async def profession_settle(self) -> ProfessionSettleResult: ...
    async def profession_enqueue(self, action_key: str, count: int) -> ProfessionQueueResult: ...
    async def profession_supply_equip(
        self, supply_type: str, item_key: str
    ) -> ProfessionSupplyResult: ...
    async def daily_claim(self, point: int) -> RewardClaimResult: ...
    async def quest_claim(self, quest_key: str) -> RewardClaimResult: ...
    async def achievement_claim(self, achievement_key: str) -> RewardClaimResult: ...
    async def codex_claim(self, reward_key: str) -> RewardClaimResult: ...
    async def mail_claim(self, mail_id: str) -> RewardClaimResult: ...


class AccountRateLimiter:
    def __init__(self, spacing_seconds: float) -> None:
        self._spacing_seconds = spacing_seconds
        self._lock = asyncio.Lock()
        self._next_allowed_at = 0.0

    async def __aenter__(self) -> None:
        async with self._lock:
            delay = self._next_allowed_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_allowed_at = time.monotonic() + self._spacing_seconds

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


def safe_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    if isinstance(payload.get("code"), str):
        return payload["code"]
    return None


def parse_retry_after(headers: httpx.Headers) -> float | None:
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


def redact_response_metadata(response: httpx.Response) -> dict[str, object]:
    return {
        "status_code": response.status_code,
        "headers": redact(dict(response.headers)),
    }


class HttpGameClient:
    def __init__(
        self,
        settings: Settings,
        *,
        session_token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        request_spacing_seconds: float = 0.1,
    ) -> None:
        if request_spacing_seconds < 0:
            raise ValueError("request spacing cannot be negative")
        self._base_url = settings.game_base_url.rstrip("/")
        self._session_token = session_token
        self._http = http_client or httpx.AsyncClient()
        self._owns_http_client = http_client is None
        self._timeout = timeout
        self._rate_limiter = AccountRateLimiter(request_spacing_seconds)

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def _request(
        self, operation: OperationName, response: type[T], body: BaseModel | None = None
    ) -> T:
        spec = REGISTRY[operation]
        payload = None if body is None else body.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        headers = {"Accept": "application/json"}
        if operation != "login" and self._session_token is not None:
            headers["Authorization"] = f"Bearer {self._session_token}"
        request_kwargs = {} if payload is None else {"json": payload}
        attempts = 1 if spec.mutation else 3

        for attempt in range(attempts):
            try:
                async with self._rate_limiter:
                    result = await self._http.request(
                        spec.method,
                        self._base_url + spec.path,
                        headers=headers,
                        timeout=self._timeout,
                        **request_kwargs,
                    )
                if result.status_code in {401, 403}:
                    raise SessionRejected()
                if result.status_code == 426:
                    raise ContractChanged()
                error_code = safe_error_code(result)
                if error_code == "inventory_full":
                    raise InventoryFull()
                if error_code == "insufficient_resource":
                    raise InsufficientResource.from_redacted_response(result)
                if result.status_code == 409:
                    raise GameConflict(error_code)
                if result.status_code == 429:
                    raise GameRateLimited(parse_retry_after(result.headers))
                if spec.mutation and result.status_code >= 500:
                    raise AmbiguousMutation(operation)
                result.raise_for_status()
                try:
                    data = result.json()["data"]
                    return response.model_validate(data)
                except (KeyError, TypeError, ValueError, ValidationError) as exc:
                    raise GameSchemaMismatch(operation, redact_response_metadata(result)) from exc
            except httpx.TimeoutException as exc:
                if spec.mutation:
                    raise AmbiguousMutation(operation) from exc
                if attempt == attempts - 1:
                    raise GameUnavailable(operation) from exc
                await asyncio.sleep((2**attempt) * 0.1 + random.random() * 0.1)
        raise AssertionError("unreachable")

    async def login(self, username: str, password: str) -> LoginResult:
        return await self._request("login", LoginResult, LoginRequest(username=username, password=password))

    async def bootstrap(self) -> BootstrapState:
        return await self._request("bootstrap", BootstrapState)

    async def catalog(self) -> Catalog:
        return await self._request("catalog", Catalog)

    async def idle_summary(self) -> IdleSummary:
        return await self._request("idle_summary", IdleSummary)

    async def view_sections(
        self, sections: tuple[ViewSection, ...], section_etags: dict[ViewSection, str] | None = None
    ) -> ViewSections:
        return await self._request(
            "view_sections",
            ViewSections,
            ViewSectionsRequest(sections=sections, section_etags=section_etags),
        )

    async def idle_collect(self) -> IdleCollectResult:
        return await self._request("idle_collect", IdleCollectResult)

    async def boss_preview(self, request: BossPreviewRequest) -> BossPreview:
        return await self._request("boss_preview", BossPreview, request)

    async def boss_challenge(self, request: BossChallengeRequest) -> BossChallengeResult:
        return await self._request("boss_challenge", BossChallengeResult, request)

    async def boss_assist(self, boss_key: str) -> BossAssistResult:
        return await self._request("boss_assist", BossAssistResult, BossAssistRequest(boss_key=boss_key))

    async def profession_settle(self) -> ProfessionSettleResult:
        return await self._request("profession_settle", ProfessionSettleResult)

    async def profession_enqueue(self, action_key: str, count: int) -> ProfessionQueueResult:
        return await self._request(
            "profession_enqueue",
            ProfessionQueueResult,
            ProfessionEnqueueRequest(action_key=action_key, count=count),
        )

    async def profession_supply_equip(self, supply_type: str, item_key: str) -> ProfessionSupplyResult:
        return await self._request(
            "profession_supply_equip",
            ProfessionSupplyResult,
            ProfessionSupplyEquipRequest(supply_type=supply_type, item_key=item_key),
        )

    async def daily_claim(self, point: int) -> RewardClaimResult:
        return await self._request("daily_claim", RewardClaimResult, DailyClaimRequest(point=point))

    async def quest_claim(self, quest_key: str) -> RewardClaimResult:
        return await self._request("quest_claim", RewardClaimResult, QuestClaimRequest(quest_key=quest_key))

    async def achievement_claim(self, achievement_key: str) -> RewardClaimResult:
        return await self._request(
            "achievement_claim", RewardClaimResult, AchievementClaimRequest(achievement_key=achievement_key)
        )

    async def codex_claim(self, reward_key: str) -> RewardClaimResult:
        return await self._request("codex_claim", RewardClaimResult, CodexClaimRequest(reward_key=reward_key))

    async def mail_claim(self, mail_id: str) -> RewardClaimResult:
        return await self._request("mail_claim", RewardClaimResult, MailClaimRequest(mail_id=mail_id))
