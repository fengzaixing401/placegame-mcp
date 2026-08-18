import json
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from placegame.errors import AmbiguousMutation, GameConflict, SessionRejected
from placegame.game.client import GameApi
from placegame.game.schemas import (
    BossAssistResult,
    BossChallengeRequest,
    BossChallengeResult,
    BossPreview,
    BossPreviewRequest,
    BootstrapState,
    Catalog,
    IdleCollectResult,
    IdleSummary,
    LoginResult,
    ProfessionQueueResult,
    ProfessionSettleResult,
    ProfessionSupplyResult,
    RewardClaimResult,
    ViewSections,
)


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    json_body: Any


@dataclass(frozen=True)
class RegisteredResponse:
    status_code: int
    body: Any
    headers: Mapping[str, str]


_CREDENTIAL_KEY_PARTS = (
    "password",
    "auth",
    "token",
    "secret",
    "authorization",
    "cookie",
    "api-key",
    "api_key",
    "apikey",
    "credential",
)


def _redact_credentials(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]"
            if isinstance(key, str)
            and any(part in key.lower() for part in _CREDENTIAL_KEY_PARTS)
            else _redact_credentials(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_credentials(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_credentials(item) for item in value)
    return value


class FakeGameServer:
    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self._routes: dict[tuple[str, str], RegisteredResponse] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self._timeouts: dict[tuple[str, str], int] = {}
        self.timeout_delay_seconds = 0.2

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("fake game server is not running")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def register(
        self,
        method: str,
        path: str,
        body: Any,
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if not path.startswith("/api/"):
            raise ValueError("fake game routes must be registered under /api/")
        self._routes[(method.upper(), path)] = RegisteredResponse(
            status_code, body, dict(headers or {})
        )

    def timeout(self, method: str, path: str, *, count: int = 1) -> None:
        if not path.startswith("/api/"):
            raise ValueError("fake game timeout paths must be under /api/")
        if count < 1:
            raise ValueError("fake game timeout count must be positive")
        self._timeouts[(method.upper(), path)] = count

    def __enter__(self) -> "FakeGameServer":
        fake = self

        class RequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self._handle()

            def do_POST(self) -> None:  # noqa: N802
                self._handle()

            def do_PUT(self) -> None:  # noqa: N802
                self._handle()

            def do_DELETE(self) -> None:  # noqa: N802
                self._handle()

            def _handle(self) -> None:
                path = urlsplit(self.path).path
                content_length = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(content_length) if content_length else b""
                try:
                    json_body = json.loads(payload) if payload else None
                except json.JSONDecodeError:
                    json_body = None

                recorded_headers = _redact_credentials(
                    {key.lower(): value for key, value in self.headers.items()}
                )
                fake.requests.append(
                    RecordedRequest(
                        self.command,
                        path,
                        recorded_headers,
                        _redact_credentials(json_body),
                    )
                )

                route = (self.command, path)
                remaining_timeouts = fake._timeouts.get(route, 0)
                if remaining_timeouts:
                    if remaining_timeouts == 1:
                        del fake._timeouts[route]
                    else:
                        fake._timeouts[route] = remaining_timeouts - 1
                    time.sleep(fake.timeout_delay_seconds)

                response = fake._routes.get(route)
                if response is None:
                    self._send_json(404, {"detail": "not found"})
                    return
                self._send_json(response.status_code, response.body, response.headers)

            def _send_json(
                self,
                status_code: int,
                body: Any,
                headers: Mapping[str, str] | None = None,
            ) -> None:
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except BrokenPipeError:
                    pass

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join()


@dataclass
class _FakeAccountState:
    account_id: UUID
    username: str | None
    password: str | None
    token: str
    accumulated_seconds: int = 3600
    capacity_seconds: int = 43200


class FakeGameApiFactory:
    """Account-aware typed fake; secrets are never copied into request recordings."""

    def __init__(self) -> None:
        self._states_by_id: dict[UUID, _FakeAccountState] = {}
        self._states_by_token: dict[str, _FakeAccountState] = {}
        self._rejected_passwords: set[str] = set()
        self._rejected_accounts: set[UUID] = set()
        self._ambiguous: set[tuple[UUID, str]] = set()
        self._conflicts: dict[tuple[UUID, str], int] = {}
        self._mutation_counts: dict[tuple[UUID, str], int] = {}
        self._bootstrap_counts: dict[UUID, int] = {}
        self.login_count = 0

    def register_credentials(
        self, account_id: UUID, username: str, password: str, token: str
    ) -> None:
        state = _FakeAccountState(account_id, username, password, token)
        self._states_by_id[account_id] = state
        self._states_by_token[token] = state

    def register_token(self, account_id: UUID, token: str) -> None:
        state = _FakeAccountState(account_id, None, None, token)
        self._states_by_id[account_id] = state
        self._states_by_token[token] = state

    def bind_account_id(self, registered_id: UUID, managed_id: UUID) -> None:
        state = self._states_by_id.pop(registered_id)
        state.account_id = managed_id
        self._states_by_id[managed_id] = state
        if registered_id in self._bootstrap_counts:
            self._bootstrap_counts[managed_id] = self._bootstrap_counts.pop(registered_id)

    def reject_login(self, password: str) -> None:
        self._rejected_passwords.add(password)

    def reject_account_session(self, account_id: UUID) -> None:
        self._rejected_accounts.add(account_id)

    def allow_account_session(self, account_id: UUID) -> None:
        self._rejected_accounts.discard(account_id)

    def commit_then_timeout(self, operation: str, account_id: UUID) -> None:
        self._ambiguous.add((account_id, operation))

    def conflict(self, operation: str, account_id: UUID, *, count: int) -> None:
        self._conflicts[(account_id, operation)] = count

    def mutation_count(self, operation: str, account_id: UUID | None = None) -> int:
        if account_id is not None:
            return self._mutation_counts.get((account_id, operation), 0)
        return sum(
            count
            for (candidate_id, candidate_operation), count in self._mutation_counts.items()
            if candidate_operation == operation and candidate_id in self._states_by_id
        )

    def bootstrap_count(self, account_id: UUID) -> int:
        return self._bootstrap_counts.get(account_id, 0)

    def __call__(self, session_token: str | None) -> GameApi:
        return _FakeGameApi(self, session_token)

    def _state_for_token(self, token: str | None) -> _FakeAccountState:
        state = self._states_by_token.get(token or "")
        if state is None or state.account_id in self._rejected_accounts:
            raise SessionRejected()
        return state


class _FakeGameApi:
    def __init__(self, factory: FakeGameApiFactory, session_token: str | None) -> None:
        self._factory = factory
        self._session_token = session_token

    async def login(self, username: str, password: str) -> LoginResult:
        self._factory.login_count += 1
        if password in self._factory._rejected_passwords:
            raise SessionRejected()
        for state in self._factory._states_by_id.values():
            if state.username == username and state.password == password:
                self._factory.allow_account_session(state.account_id)
                return LoginResult(token=state.token)
        raise SessionRejected()

    async def bootstrap(self) -> BootstrapState:
        state = self._factory._state_for_token(self._session_token)
        self._factory._bootstrap_counts[state.account_id] = (
            self._factory._bootstrap_counts.get(state.account_id, 0) + 1
        )
        return BootstrapState(accountId=str(state.account_id))

    async def idle_summary(self) -> IdleSummary:
        state = self._factory._state_for_token(self._session_token)
        return IdleSummary(
            accumulatedSeconds=state.accumulated_seconds,
            capacitySeconds=state.capacity_seconds,
        )

    async def catalog(self) -> Catalog:
        raise NotImplementedError("catalog is not needed by the account fake")

    async def view_sections(
        self,
        sections: tuple[str, ...],
        section_etags: dict[str, str] | None = None,
    ) -> ViewSections:
        raise NotImplementedError("view sections are not needed by the account fake")

    async def idle_collect(self) -> IdleCollectResult:
        state = self._factory._state_for_token(self._session_token)
        key = (state.account_id, "idle_collect")
        self._factory._mutation_counts[key] = self._factory._mutation_counts.get(key, 0) + 1
        remaining_conflicts = self._factory._conflicts.get(key, 0)
        if remaining_conflicts:
            self._factory._conflicts[key] = remaining_conflicts - 1
            raise GameConflict("conflict")
        state.accumulated_seconds = 0
        if key in self._factory._ambiguous:
            self._factory._ambiguous.remove(key)
            raise AmbiguousMutation("idle_collect")
        return IdleCollectResult(collected=True)

    async def boss_preview(self, request: BossPreviewRequest) -> BossPreview:
        raise NotImplementedError("boss preview is not needed by the account fake")

    async def boss_challenge(
        self, request: BossChallengeRequest
    ) -> BossChallengeResult:
        raise NotImplementedError("boss challenge is not needed by the account fake")

    async def boss_assist(self, boss_key: str) -> BossAssistResult:
        raise NotImplementedError("boss assist is not needed by the account fake")

    async def profession_settle(self) -> ProfessionSettleResult:
        raise NotImplementedError("profession settlement is not needed by the account fake")

    async def profession_enqueue(
        self, action_key: str, count: int
    ) -> ProfessionQueueResult:
        raise NotImplementedError("profession enqueue is not needed by the account fake")

    async def profession_supply_equip(
        self, supply_type: str, item_key: str
    ) -> ProfessionSupplyResult:
        raise NotImplementedError("profession supplies are not needed by the account fake")

    async def daily_claim(self, point: int) -> RewardClaimResult:
        raise NotImplementedError("daily claims are not needed by the account fake")

    async def quest_claim(self, quest_key: str) -> RewardClaimResult:
        raise NotImplementedError("quest claims are not needed by the account fake")

    async def achievement_claim(self, achievement_key: str) -> RewardClaimResult:
        raise NotImplementedError("achievement claims are not needed by the account fake")

    async def codex_claim(self, reward_key: str) -> RewardClaimResult:
        raise NotImplementedError("codex claims are not needed by the account fake")

    async def mail_claim(self, mail_id: str) -> RewardClaimResult:
        raise NotImplementedError("mail claims are not needed by the account fake")
