# PlaceGame MCP Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Python 3.12 modular monolith that safely manages multiple PlaceGame accounts, runs durable Beijing-time automation while agents are offline, and exposes a scoped Streamable HTTP MCP endpoint using only typed game HTTP operations.

**Architecture:** FastAPI hosts the admin/API and MCP adapters, while domain services (accounts, policy, plans, jobs, audit, and game client) communicate through explicit protocols. PostgreSQL 16 is the source of durable state; one scheduler lease holder dispatches jobs and per-account PostgreSQL advisory locks serialize mutations. The GitHub/OneSSH deployment plan owns production image, Compose, registry, and edge integration.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 async, asyncpg, Alembic, httpx, cryptography (AES-256-GCM), argon2-cffi, pyotp, official Python MCP SDK (`mcp>=1.12,<2`), structlog, PostgreSQL 16, and pytest/pytest-asyncio/Hypothesis.

## Global Constraints

- All game requests target `https://game.placegame.cn/api/*` through registered typed methods; callers cannot supply a URL, path, or arbitrary request body.
- Game automation uses direct HTTP APIs only and never browser clicks, DOM access, Playwright, or image recognition.
- The Singapore production topology and edge are defined by `2026-08-17-placegame-github-onessh-deployment-design.md`: `app` binds to `127.0.0.1:18080`, a dedicated PostgreSQL publishes no host port, and existing 1Panel OpenResty remains the external edge.
- The application runs one active scheduler lease holder; normal account concurrency is four and world-boss work can use all configured slots.
- At least ten configured accounts must operate independently without credential, state, plan, or job crossover.
- Dispatch and displayed schedules use `Asia/Shanghai` (`UTC+8`) regardless of VPS local timezone.
- A 256-bit master key is mounted as a Docker secret; usernames, passwords, session tokens, and TOTP secrets use AES-256-GCM with a fresh nonce and record-bound AAD.
- Backups include PostgreSQL; the encryption master key is backed up through a separate operator-controlled channel, while existing 1Panel configuration remains operator-managed.
- Credential-mode sessions renew when absent, rejected, or within 24 hours of expiry; token-only accounts pause with `session_refresh_required` when renewal is required.
- Every mutation acquires the account advisory lock, refreshes authoritative state, rechecks policy and plan preconditions, performs one typed request, verifies the state transition, and audits the result.
- Sanitized account snapshots have a five-minute logical TTL and are never the sole precondition for a mutation.
- Read timeouts retry at most three times with jitter; mutation timeouts reconcile state before any retry and never blindly repeat a request.
- HTTP 426 activates a global game-mutation pause and update-required alert; a schema mismatch stops the affected operation with redacted metadata; a conflict is reconciled at most twice before the job is deferred one minute.
- Inventory-full responses invoke only the safe inventory planner; insufficient-resource results are skipped with exact owned, cost, reserve, and projected-remainder figures.
- Permanent profession specialization is read-only and no service calls `/api/professions/select`.
- Default idle collection is 11 hours 30 minutes, emergency retries begin at 11 hours 50 minutes at 30/60/120-second intervals, and the server capacity (currently 12 hours) is never exceeded.
- Personal bosses use the shared pool's five free daily attempts only, evaluate `nightmare` → `hard` → `normal`, and challenge only `predictedWin=true` with chance at least 80 by default.
- World-boss windows are Beijing time 10:00–11:00, 16:00–17:00, and 20:00–21:00; eligible active instances use `POST /api/boss/assist` until `remainingAttemptCount` is zero.
- MCP tokens are independently revocable, scope checked, account allowlisted, hashed at rest, and shown in full only once.
- A batch MCP partial failure is reported per account and never rolls back successful work on another account.
- Audit records retain actor, source, account, plan, action, cost, result, and correlation ID for 90 days by default; credentials, authorization headers, TOTP secrets, and full MCP tokens never appear in logs or responses.
- Excluded operations are browser automation, arbitrary HTTP pass-through, market automation, enhancement/reforging/quality upgrades/unbinding/inheritance, and automatic permanent-specialization changes.
- Automated tests use redacted fixtures and the fake game server; a real account is touched only by a separately invoked read-only integration profile.
- Safe reward mutations are limited to the observed fixed pairs: daily `{point}`, quest `{questKey}`, achievement `{achievementKey}`, codex `{rewardKey}`, and single-mail `{mailId}` claims; claim-all and every unrelated reward endpoint remain unregistered.

---

## Execution Order

After the deployment plan's GitHub repository bootstrap task, execute this plan first. It delivers the core service and explicit inventory/WebUI extension ports; until the inventory plan is installed, reward-generating scheduler actions fail closed with `inventory_safety_unavailable` and inventory MCP handlers are not advertised. Production image and server deployment tasks run only after Core, Inventory, and WebUI pass.

## File Map

Create or modify these files in the order below. The inventory and WebUI plans extend the protocols marked as extension points; they do not replace them.

- Create: `pyproject.toml`, `uv.lock` — pinned runtime, test, lint, and migration dependencies.
- Create: `.env.example` — non-secret configuration names and safe defaults.
- Create: `alembic.ini`, `migrations/env.py`, `migrations/versions/001_core.py` — async migration setup and core tables.
- Create: `src/placegame/__init__.py`, `src/placegame/config.py`, `src/placegame/app.py` — settings and ASGI factory.
- Create: `src/placegame/contracts.py`, `src/placegame/errors.py` — cross-module enums, actor/target types, and stable errors.
- Create: `src/placegame/db.py`, `src/placegame/models.py` — async session factory and SQLAlchemy models.
- Create: `src/placegame/security/crypto.py`, `src/placegame/security/redaction.py`, `src/placegame/security/tokens.py` — secret encryption, safe logging, and MCP token hashing.
- Create: `src/placegame/game/schemas.py`, `src/placegame/game/registry.py`, `src/placegame/game/client.py` — typed request/response models and allowlisted HTTP client.
- Create: `src/placegame/accounts/repository.py`, `src/placegame/accounts/locks.py`, `src/placegame/accounts/reconcile.py`, `src/placegame/accounts/service.py` — account lifecycle, advisory locks, session renewal, and mutation reconciliation.
- Create: `src/placegame/policy/models.py`, `src/placegame/policy/plans.py`, `src/placegame/policy/ports.py`, `src/placegame/policy/engine.py`, `src/placegame/boss_optimizer.py`, `src/placegame/rewards.py` — validated policies, fail-closed extension ports, and plan selection for idle, bosses, professions, and safe rewards.
- Create: `src/placegame/jobs/clock.py`, `src/placegame/jobs/store.py`, `src/placegame/jobs/handlers.py`, `src/placegame/jobs/scheduler.py` — durable jobs, leases, idempotency, time windows, and workers.
- Create: `src/placegame/audit.py`, `src/placegame/observability.py`, `src/placegame/health.py` — append-only audit, JSON logs, and health/readiness endpoints.
- Create: `src/placegame/mcp/auth.py`, `src/placegame/mcp/context.py`, `src/placegame/mcp/server.py`, `src/placegame/mcp/tools.py` — Streamable HTTP MCP authentication and tools.
- Create: `tests/conftest.py`, `tests/fakes/game_server.py`, `tests/fakes/clock.py` — deterministic fixtures and an in-process fake game API.
- Create: `tests/unit/test_app_bootstrap.py`, `tests/unit/test_security.py`, `tests/unit/test_game_client.py`, `tests/unit/test_accounts.py`, `tests/unit/test_policy.py`, `tests/unit/test_scheduler.py`, `tests/unit/test_mcp.py` — focused unit/contract tests.
- Create: `tests/integration/test_migrations.py`, `tests/integration/test_account_isolation.py`, `tests/integration/test_core_acceptance.py` — PostgreSQL-backed migration, isolation, and idempotency acceptance tests.

## Interfaces Shared With Later Plans

The following signatures are frozen before implementation so inventory and WebUI tasks can depend on them:

```python
# src/placegame/contracts.py
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

ActorKind = Literal["scheduler", "webui", "mcp"]

@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    actor_id: str
    scopes: frozenset[str] = frozenset()

@dataclass(frozen=True)
class AccountTarget:
    account_id: UUID | None = None
    account_ids: tuple[UUID, ...] = ()
    all_enabled: bool = False

    def validate(self) -> None:
        if sum(bool(x) for x in (self.account_id, self.account_ids, self.all_enabled)) != 1:
            raise ValueError("exactly one account selector is required")
```

```python
# src/placegame/accounts/service.py
@dataclass
class LockedAccount:
    account_id: UUID
    api: GameApi
    policy: VersionedPolicy
    snapshot: AccountSnapshot

class AccountService:
    async def add_credentials(self, label: str, username: str, password: str, *, actor: Actor) -> ManagedAccount: ...
    async def add_token_only(self, label: str, session_token: str, *, actor: Actor) -> ManagedAccount: ...
    async def update_label(self, account_id: UUID, label: str, *, actor: Actor) -> ManagedAccount: ...
    async def update_credentials(self, account_id: UUID, username: str | None, password: str, *, actor: Actor) -> ManagedAccount: ...
    async def update_token_only(self, account_id: UUID, session_token: str, *, actor: Actor) -> ManagedAccount: ...
    async def enable(self, account_id: UUID, *, actor: Actor) -> None: ...
    async def disable(self, account_id: UUID, *, actor: Actor) -> None: ...
    async def pause(self, account_id: UUID, reason: str, *, actor: Actor) -> None: ...
    async def resume(self, account_id: UUID, *, actor: Actor) -> None: ...
    async def disable_drain_remove(self, account_id: UUID, *, actor: Actor) -> RemovalReceipt: ...
    async def get(self, account_id: UUID) -> ManagedAccount: ...
    async def ensure_session(self, account_id: UUID, *, actor: Actor) -> SessionState: ...
    def locked(self, account_id: UUID) -> AbstractAsyncContextManager[LockedAccount]: ...
    async def snapshot(self, account_id: UUID, *, actor: Actor) -> AccountSnapshot: ...
    async def mutate(
        self,
        account_id: UUID,
        operation: Callable[[GameApi], Awaitable[T]],
        *,
        actor: Actor,
        plan_id: UUID | None = None,
        verify: Callable[[GameApi, T], Awaitable[bool]] | None = None,
    ) -> MutationOutcome[T]: ...
```

```python
# src/placegame/policy/plans.py
class PlanStore(Protocol):
    async def create(self, draft: ActionPlanDraft) -> ActionPlan: ...
    async def get_for_update(self, plan_id: UUID, account_id: UUID) -> ActionPlan: ...
    async def mark_executing(self, plan_id: UUID, expected_version: int) -> None: ...
    async def finish(self, plan_id: UUID, status: PlanStatus, result: dict) -> None: ...
```

### Task 1: Bootstrap the Application and Deterministic Test Harness

**Files:**
- Create: `pyproject.toml`
- Create: `uv.lock`
- Create: `.env.example`
- Create: `src/placegame/__init__.py`
- Create: `src/placegame/config.py`
- Create: `src/placegame/app.py`
- Create: `tests/conftest.py`
- Create: `tests/fakes/game_server.py`
- Test: `tests/unit/test_app_bootstrap.py`

**Interfaces:**
- Produces `Settings.from_env() -> Settings`, `Settings.read_database_url() -> str`, `create_app(settings: Settings | None = None) -> FastAPI`, and `FakeGameServer.url` for all later tests.

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/unit/test_app_bootstrap.py
from fastapi.testclient import TestClient
from placegame.app import create_app

def test_health_endpoint_is_available(settings):
    response = TestClient(create_app(settings)).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `uv run pytest tests/unit/test_app_bootstrap.py::test_health_endpoint_is_available -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'placegame'`.

- [ ] **Step 3: Add the minimal package and dependency lock**

Create `pyproject.toml` with Python and tool constraints, then add the factory:

```toml
[project]
name = "placegame-mcp"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.115,<1", "uvicorn[standard]>=0.30,<1", "pydantic>=2.8,<3",
  "pydantic-settings>=2.4,<3", "sqlalchemy[asyncio]>=2.0.36,<3", "asyncpg>=0.30,<1",
  "alembic>=1.14,<2", "httpx>=0.27,<1", "cryptography>=43,<45",
  "argon2-cffi>=23.1,<26", "pyotp>=2.9,<3", "mcp>=1.12,<2", "structlog>=24,<26",
]
[dependency-groups]
dev = [
  "pytest>=8.3,<9", "pytest-asyncio>=0.24,<1", "hypothesis>=6.115,<7",
  "respx>=0.22,<1", "pyright>=1.1.390,<2", "pyyaml>=6,<7", "testcontainers[postgres]>=4.8,<5"
]
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

```python
# src/placegame/config.py
from pathlib import Path
from urllib.parse import urlparse
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")
    database_url: str = "postgresql+asyncpg://placegame:placegame@postgres:5432/placegame"
    database_url_file: Path | None = Field(None, alias="PLACEGAME_DATABASE_URL_FILE")
    game_base_url: str = "https://game.placegame.cn"
    test_mode: bool = False
    master_key_b64: SecretStr | None = Field(None, alias="PLACEGAME_MASTER_KEY_B64")
    master_key_file: Path = Field(Path("/run/secrets/placegame_master_key"), alias="PLACEGAME_MASTER_KEY_FILE")
    scheduler_lease_seconds: int = 30
    max_account_concurrency: int = 4
    audit_retention_days: int = 90

    @model_validator(mode="after")
    def fixed_game_origin(self) -> "Settings":
        if not self.test_mode and self.game_base_url.rstrip("/") != "https://game.placegame.cn":
            raise ValueError("production game_base_url must be https://game.placegame.cn")
        if self.test_mode and urlparse(self.game_base_url).hostname not in {"127.0.0.1", "localhost", "testserver", "::1"}:
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
```

```python
# src/placegame/app.py
from fastapi import FastAPI
from .config import Settings

def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="PlaceGame MCP", docs_url=None, redoc_url=None)
    app.state.settings = settings or Settings.from_env()
    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}
    return app
```

Add `tests/conftest.py` with `TEST_MASTER_KEY_B64`, a 32-byte test key, and a `settings` fixture that sets `test_mode=True` only for the loopback fake server; `FakeGameServer` must expose only registered `/api/*` routes and record requests without storing authorization values. Run `uv lock` after creating `pyproject.toml` and commit the resulting `uv.lock`.

- [ ] **Step 4: Run the test and verify it passes**

Run: `uv run pytest tests/unit/test_app_bootstrap.py::test_health_endpoint_is_available -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit the bootstrap checkpoint**

```bash
git add pyproject.toml uv.lock .env.example src/placegame tests/unit/test_app_bootstrap.py tests/conftest.py tests/fakes/game_server.py
git commit -m "chore: bootstrap placegame core service"
```

### Task 2: Add PostgreSQL Persistence, Encryption, and Redaction

**Files:**
- Create: `src/placegame/db.py`
- Create: `src/placegame/models.py`
- Create: `src/placegame/contracts.py`
- Create: `src/placegame/errors.py`
- Create: `src/placegame/security/crypto.py`
- Create: `src/placegame/security/redaction.py`
- Create: `src/placegame/security/tokens.py`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/001_core.py`
- Test: `tests/unit/test_security.py`
- Test: `tests/integration/test_migrations.py`

**Interfaces:**
- Produces `SecretBox.encrypt/decrypt`, `redact(value)`, `token_digest(token)`, SQLAlchemy models for `game_accounts`, `account_policies`, `account_snapshots`, `jobs`, `job_runs`, `action_plans`, `mcp_tokens`, `audit_events`, and a session factory `get_session()`.

- [ ] **Step 1: Write failing security and schema tests**

```python
# tests/unit/test_security.py
def test_secret_box_round_trip_and_aad_binding(secret_box):
    blob = secret_box.encrypt("密码", aad="account/1/password")
    assert secret_box.decrypt(blob, aad="account/1/password") == "密码"
    with pytest.raises(InvalidSecret):
        secret_box.decrypt(blob, aad="account/2/password")

def test_redaction_removes_credentials_and_authorization():
    assert redact({"password": "p", "Authorization": "Bearer abc", "ok": 1}) == {
        "password": "[REDACTED]", "Authorization": "[REDACTED]", "ok": 1
    }

def test_token_digest_is_stable_but_full_token_is_not_stored():
    token = "pgm_" + "a" * 48
    assert token_digest(token) == token_digest(token)
    assert token not in token_digest(token)
```

```python
# tests/integration/test_migrations.py
def test_migrations_upgrade_and_match_metadata(postgres_url):
    config = alembic_config(database_url=postgres_url)
    command.upgrade(config, "head")
    command.check(config)
```

- [ ] **Step 2: Run the tests and verify the expected failure**

Run: `uv run pytest tests/unit/test_security.py tests/integration/test_migrations.py -q`

Expected: FAIL because `SecretBox`, `redact`, `token_digest`, and migration metadata do not exist.

- [ ] **Step 3: Implement encryption, redaction, token hashing, and models**

At startup construct `SecretBox(settings.read_master_key_b64().get_secret_value())`; production reads the Docker-secret file and tests inject the environment value. The async engine is constructed from `settings.read_database_url()` so deployment can mount the full URL as a secret file without exposing it in Compose environment values. Use a 32-byte decoded master key, 12-byte random nonce, and AES-GCM associated data containing the record identity:

```python
# src/placegame/security/crypto.py
import base64, os
from dataclasses import dataclass
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from placegame.errors import InvalidSecret

@dataclass(frozen=True)
class EncryptedSecret:
    nonce: bytes
    ciphertext: bytes

class SecretBox:
    def __init__(self, key_b64: str):
        key = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
        if len(key) != 32:
            raise ValueError("PLACEGAME_MASTER_KEY_B64 must decode to 32 bytes")
        self._key = key

    def encrypt(self, value: str, *, aad: str) -> EncryptedSecret:
        nonce = os.urandom(12)
        return EncryptedSecret(nonce, AESGCM(self._key).encrypt(nonce, value.encode(), aad.encode()))

    def decrypt(self, blob: EncryptedSecret, *, aad: str) -> str:
        try:
            return AESGCM(self._key).decrypt(blob.nonce, blob.ciphertext, aad.encode()).decode()
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise InvalidSecret("encrypted value cannot be decrypted") from exc
```

`redact` recursively replaces case-insensitive keys containing `password`, `token`, `secret`, `authorization`, `cookie`, or `master_key`, and truncates correlation-safe strings only after redaction. `token_digest` returns `hashlib.sha256(token.encode()).hexdigest()`.

Define the schema exactly:

- `game_accounts`: UUID `id`, `label`, encrypted nullable `game_username`, `auth_mode`, encrypted nullable password, encrypted session token, `session_expires_at`, `enabled`, nullable `paused_reason`, monotonic `policy_version`, create/update/last-success/last-error timestamps, and an authentication-failure-window counter.
- `account_policies`: one strict JSONB document plus version and timestamps per account; `account_snapshots`: sanitized JSONB, state fingerprint, fetched/expiry timestamps, never a mutation precondition by itself.
- `jobs`: kind, schedule, timezone, enabled, next run and misfire policy; `job_runs`: dispatch/lease/attempt/result/retry fields plus unique `(account_id, idempotency_key)`.
- `action_plans`: state fingerprint, policy version, proposed actions, costs, risk, expiry, confirmation and execution state; `mcp_tokens`: prefix, unique SHA-256 digest, scopes, account allowlist, expiry, last use and revocation, never the full token.
- `audit_events`: append-only actor/source/account/plan/action/cost/result/correlation and redacted before/after JSON; `scheduler_leases`: one row keyed by `name='default'`.

Encode each encrypted column as version byte + 12-byte nonce + ciphertext/tag in `BYTEA`; derive AAD from `table/record-id/column`. Add `account_policies.policy_version`, snapshot expiry, action-plan expiry, job due/lease, token digest/expiry, and audit retention indexes so current state is queryable without table scans.

- [ ] **Step 4: Run security tests and migration checks**

Run: `uv run pytest tests/unit/test_security.py tests/integration/test_migrations.py -q`

Expected: security tests pass and the Testcontainers PostgreSQL migration reaches `head` with `No new upgrade operations detected`.

- [ ] **Step 5: Commit the persistence checkpoint**

```bash
git add src/placegame/db.py src/placegame/models.py src/placegame/contracts.py src/placegame/errors.py src/placegame/security alembic.ini migrations tests/unit/test_security.py tests/integration/test_migrations.py
git commit -m "feat: add encrypted core persistence"
```

### Task 3: Implement the Typed, Allowlisted Game API Client

**Files:**
- Create: `src/placegame/game/schemas.py`
- Create: `src/placegame/game/registry.py`
- Create: `src/placegame/game/client.py`
- Modify: `src/placegame/errors.py`
- Test: `tests/unit/test_game_client.py`
- Test: `tests/fakes/game_server.py`

**Interfaces:**
- Produces `GameApi` methods `login`, `bootstrap`, `catalog`, `idle_summary`, `view_sections`, `idle_collect`, `boss_preview`, `boss_challenge`, `boss_assist`, `profession_settle`, `profession_enqueue`, `profession_supply_equip`, `daily_claim`, `quest_claim`, `achievement_claim`, `codex_claim`, and `mail_claim`.

- [ ] **Step 1: Write failing client contract tests**

```python
async def test_typed_request_adds_bearer_and_redacts_recorded_headers(fake_game, game_client):
    await game_client.bootstrap()
    request = fake_game.requests[-1]
    assert request.path == "/api/client/bootstrap"
    assert request.headers["authorization"] == "[REDACTED]"

async def test_unknown_operation_cannot_be_called(game_client):
    assert not hasattr(game_client, "raw")
    assert "/api/delete-all" not in {spec.path for spec in REGISTRY.values()}

async def test_safe_reward_claims_have_fixed_paths_and_bodies(fake_game, game_client):
    await game_client.quest_claim("quest-1")
    assert fake_game.requests[-1].path == "/api/quests/claim"
    assert fake_game.requests[-1].json_body == {"questKey": "quest-1"}
    assert "mail_claim_all" not in REGISTRY

async def test_read_timeout_retries_three_times_but_mutation_timeout_is_ambiguous(fake_game, game_client):
    fake_game.fail_next_reads = 3
    with pytest.raises(GameUnavailable):
        await game_client.catalog()
    fake_game.timeout_next_mutation = True
    with pytest.raises(AmbiguousMutation):
        await game_client.idle_collect()
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/unit/test_game_client.py -q`

Expected: FAIL with missing `GameApi` methods and registry.

- [ ] **Step 3: Add typed schemas, registry, and client implementation**

Define Pydantic request models so each endpoint has a fixed path/body pair:

```python
# src/placegame/game/registry.py
REGISTRY: dict[OperationName, EndpointSpec] = {
    "login": EndpointSpec("POST", "/api/auth/login", mutation=True),
    "bootstrap": EndpointSpec("GET", "/api/client/bootstrap", mutation=False),
    "catalog": EndpointSpec("GET", "/api/client/catalog", mutation=False),
    "idle_summary": EndpointSpec("GET", "/api/client/idle-summary", mutation=False),
    "view_sections": EndpointSpec("POST", "/api/client/view-sections", mutation=False),
    "idle_collect": EndpointSpec("POST", "/api/battle/idle-collect", mutation=True),
    "boss_preview": EndpointSpec("POST", "/api/boss/preview", mutation=False),
    "boss_challenge": EndpointSpec("POST", "/api/boss/challenge", mutation=True),
    "boss_assist": EndpointSpec("POST", "/api/boss/assist", mutation=True),
    "profession_settle": EndpointSpec("POST", "/api/professions/settle", mutation=True),
    "profession_enqueue": EndpointSpec("POST", "/api/professions/queue/enqueue", mutation=True),
    "profession_supply_equip": EndpointSpec("POST", "/api/professions/supply/equip", mutation=True),
    "daily_claim": EndpointSpec("POST", "/api/daily/claim", mutation=True),
    "quest_claim": EndpointSpec("POST", "/api/quests/claim", mutation=True),
    "achievement_claim": EndpointSpec("POST", "/api/achievements/claim", mutation=True),
    "codex_claim": EndpointSpec("POST", "/api/codex/claim", mutation=True),
    "mail_claim": EndpointSpec("POST", "/api/mail/claim", mutation=True),
}
```

```python
# src/placegame/game/client.py
class BossPreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    boss_key: str = Field(alias="bossKey")
    difficulty: Literal["normal", "hard", "nightmare"]
    selected_skill_keys: list[str] = Field(alias="selectedSkillKeys", max_length=3)
    buff_key: Literal["none", "assault", "guard", "focus"] = Field(alias="buffKey")
    affix_key: str | None = Field(alias="affixKey")
    target_slot: str = Field(alias="targetSlot")
    use_material_boost: bool = Field(alias="useMaterialBoost")

class BossAssistRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    boss_key: str = Field(alias="bossKey", min_length=1)

class GameApi(Protocol):
    async def login(self, username: str, password: str) -> LoginResult: ...
    async def bootstrap(self) -> BootstrapState: ...
    async def catalog(self) -> Catalog: ...
    async def idle_summary(self) -> IdleSummary: ...
    async def view_sections(self, sections: tuple[ViewSection, ...], section_etags: dict[ViewSection, str] | None = None) -> ViewSections: ...
    async def idle_collect(self) -> IdleCollectResult: ...
    async def boss_preview(self, request: BossPreviewRequest) -> BossPreview: ...
    async def boss_challenge(self, request: BossChallengeRequest) -> BossChallengeResult: ...
    async def boss_assist(self, boss_key: str) -> BossAssistResult: ...
    async def profession_settle(self) -> ProfessionSettleResult: ...
    async def profession_enqueue(self, action_key: str, count: int) -> ProfessionQueueResult: ...
    async def profession_supply_equip(self, supply_type: str, item_key: str) -> ProfessionSupplyResult: ...
    async def daily_claim(self, point: int) -> RewardClaimResult: ...
    async def quest_claim(self, quest_key: str) -> RewardClaimResult: ...
    async def achievement_claim(self, achievement_key: str) -> RewardClaimResult: ...
    async def codex_claim(self, reward_key: str) -> RewardClaimResult: ...
    async def mail_claim(self, mail_id: str) -> RewardClaimResult: ...

class HttpGameClient:
    async def _request(self, operation: OperationName, response: type[T], body: BaseModel | None = None) -> T:
        spec = REGISTRY[operation]
        payload = None if body is None else body.model_dump(mode="json", by_alias=True, exclude_none=True)
        headers = {"Accept": "application/json"}
        if operation != "login":
            headers["Authorization"] = f"Bearer {self._session_token}"
        request_kwargs = {} if payload is None else {"json": payload}
        attempts = 1 if spec.mutation else 3
        for attempt in range(attempts):
            try:
                async with self._rate_limiter:
                    result = await self._http.request(spec.method, self._base_url + spec.path, headers=headers, timeout=self._timeout, **request_kwargs)
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
                    return response.model_validate(result.json()["data"])
                except (KeyError, ValueError, ValidationError) as exc:
                    raise GameSchemaMismatch(operation, redact_response_metadata(result)) from exc
            except httpx.TimeoutException as exc:
                if spec.mutation:
                    raise AmbiguousMutation(operation) from exc
                if attempt == attempts - 1:
                    raise GameUnavailable(operation) from exc
                await asyncio.sleep((2 ** attempt) * 0.1 + random.random() * 0.1)
        raise AssertionError("unreachable")
```

The registry includes only the observed paths and typed operations; `BossAssistRequest(boss_key=boss.boss_key).model_dump(by_alias=True)` yields exactly `{ "bossKey": boss.boss_key }` for world collaboration. Profession settle has no body, enqueue uses exactly `{actionKey, count}`, and supply equip uses exactly `{supplyType, itemKey}`. Define separate Pydantic request models for daily `{point}`, quest `{questKey}`, achievement `{achievementKey}`, codex `{rewardKey}`, and single-mail `{mailId}` claims. `mail_claim_all`, `raw`, URL concatenation from caller input, and a generic request method are absent from the registry and public protocol. The registry's mutation flag is the single retry authority; callers cannot override it. Login omits the bearer header and gets one transport attempt, while the account service owns its bounded authentication retries. Error-envelope parsing maps inventory-full, insufficient-resource, conflict, rate-limit, and contract-change outcomes to stable typed errors without retaining raw bodies; a mutation-side 5xx is ambiguous and must reconcile just like a timeout. `HttpGameClient` records redacted metadata and enforces per-account request spacing.

- [ ] **Step 4: Run contract tests and verify they pass**

Run: `uv run pytest tests/unit/test_game_client.py -q`

Expected: all client tests pass, including exactly three read attempts and one mutation attempt.

- [ ] **Step 5: Commit the game-client checkpoint**

```bash
git add src/placegame/game src/placegame/errors.py tests/unit/test_game_client.py tests/fakes/game_server.py
git commit -m "feat: add typed placegame http client"
```

### Task 4: Add Account Lifecycle, Session Renewal, Locks, and Reconciliation

**Files:**
- Create: `src/placegame/accounts/repository.py`
- Create: `src/placegame/accounts/locks.py`
- Create: `src/placegame/accounts/reconcile.py`
- Create: `src/placegame/accounts/service.py`
- Test: `tests/unit/test_accounts.py`
- Test: `tests/integration/test_account_isolation.py`

**Interfaces:**
- `AccountService.add_credentials`, `add_token_only`, `update_label`, `update_credentials`, `update_token_only`, `enable`, `disable`, `pause`, `resume`, `disable_drain_remove`, `get`, `ensure_session`, `locked`, `snapshot`, and `mutate` use the frozen shared signatures above.

- [ ] **Step 1: Write failing lifecycle and ambiguity tests**

```python
async def test_credentials_renew_within_24_hours(account_service, fake_game):
    account = await account_service.add_credentials("a", "user", "password", actor=admin_actor)
    fake_game.expire_token_for(account.id, hours=23)
    snapshot = await account_service.snapshot(account.id, actor=admin_actor)
    assert snapshot.authenticated is True
    assert fake_game.login_count == 1

async def test_token_only_pauses_when_refresh_is_needed(account_service, token_account):
    token_account.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    await account_service.ensure_session(token_account.id, actor=scheduler_actor)
    assert (await account_service.get(token_account.id)).paused_reason == "session_refresh_required"

async def test_timeout_after_commit_is_reconciled_without_duplicate(account_service, fake_game):
    fake_game.commit_then_timeout("idle_collect")
    result = await account_service.mutate(token_account.id, lambda api: api.idle_collect(), actor=scheduler_actor, verify=idle_counter_increased)
    assert result.applied is True
    assert fake_game.mutation_count("idle_collect") == 1

async def test_disabling_one_account_blocks_only_its_new_mutations(account_service, account_a, account_b):
    await account_service.disable(account_a.id, actor=admin_actor)
    with pytest.raises(AccountDisabled):
        await account_service.mutate(account_a.id, lambda api: api.idle_collect(), actor=scheduler_actor)
    assert (await account_service.snapshot(account_b.id, actor=admin_actor)).enabled is True

async def test_credential_update_is_verified_before_old_secret_is_replaced(account_service, credential_account, fake_game):
    fake_game.reject_login("bad-password")
    with pytest.raises(AuthenticationRequired):
        await account_service.update_credentials(credential_account.id, None, "bad-password", actor=admin_actor)
    assert (await account_service.snapshot(credential_account.id, actor=admin_actor)).authenticated is True
```

- [ ] **Step 2: Run tests and verify the missing-service failure**

Run: `uv run pytest tests/unit/test_accounts.py tests/integration/test_account_isolation.py -q`

Expected: FAIL because repository, lock, and reconciliation services are not defined.

- [ ] **Step 3: Implement account locking and session lifecycle**

Use a transaction-scoped advisory lock and never hold it while resolving another account:

```python
# src/placegame/accounts/locks.py
from contextlib import asynccontextmanager
from uuid import UUID

@asynccontextmanager
async def account_lock(session: AsyncSession, account_id: UUID):
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:id, 0))"), {"id": str(account_id)})
    yield
```

`ensure_session` decrypts credentials only inside the lock, logs in when the token is absent/rejected/within 24 hours, retries failed login twice with increasing delays, and pauses only that account after three failed authentication cycles in one hour. Token-only accounts set `session_refresh_required` and emit a critical audit event. Label edits are validated and audited; credential/token edits verify `bootstrap` with the proposed secret before atomically replacing the encrypted value, and a failed verification preserves the old secret. `disable_drain_remove` disables new work, drains the account lock, cancels future jobs, deletes secrets, and preserves only tombstoned audit identity. `mutate` refreshes state, checks `enabled`/`paused_reason`, calls the typed operation once, invokes a verifier after success or any ambiguous outcome, reconciles before each of at most two conflict retries with jitter, and writes an audit event containing no secret values.

- [ ] **Step 4: Run lifecycle, isolation, and reconciliation tests**

Run: `uv run pytest tests/unit/test_accounts.py tests/integration/test_account_isolation.py -q`

Expected: all tests pass; the injected account failure leaves other account snapshots and job state unchanged.

- [ ] **Step 5: Commit the account checkpoint**

```bash
git add src/placegame/accounts tests/unit/test_accounts.py tests/integration/test_account_isolation.py
git commit -m "feat: add account locks and session reconciliation"
```

### Task 5: Implement Policy Models, Action Plans, and Boss/Profession Decisions

**Files:**
- Create: `src/placegame/policy/models.py`
- Create: `src/placegame/policy/plans.py`
- Create: `src/placegame/policy/ports.py`
- Create: `src/placegame/policy/engine.py`
- Create: `src/placegame/boss_optimizer.py`
- Create: `src/placegame/rewards.py`
- Test: `tests/unit/test_policy.py`

**Interfaces:**
- Produces `AccountPolicy` with strict unknown-field rejection, `VersionedPolicy`, `PolicyService.get/save/server_idle_capacity`, `PolicyEngine.build_idle_plan`, `build_personal_boss_plan`, `build_world_boss_plan`, `build_profession_plan`, `safe_reward_plan`, and `BossOptimizer.optimize`.

```python
class InventorySafetyPort(Protocol):
    async def before_reward_generating_action(self, locked: LockedAccount, kind: Literal["idle", "boss", "profession", "reward"], *, actor: Actor) -> PressureDecision: ...

class UnavailableInventorySafety:
    async def before_reward_generating_action(self, locked: LockedAccount, kind: Literal["idle", "boss", "profession", "reward"], *, actor: Actor) -> PressureDecision:
        return PressureDecision(allow=False, reason="inventory_safety_unavailable")
```

- [ ] **Step 1: Write failing policy tests**

```python
def test_policy_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        AccountPolicy.model_validate({"boss_min_chance": 80, "unexpected": True})

def test_idle_threshold_never_exceeds_server_capacity():
    plan = engine.build_idle_plan(summary(valid_minutes=710, capacity_minutes=720), policy)
    assert plan.action == "collect"
    assert plan.threshold_minutes == 690

def test_personal_boss_orders_nightmare_and_requires_win():
    plan = engine.build_personal_boss_plan(bosses, policy)
    assert plan.difficulty == "nightmare"
    assert plan.preview.predicted_win is True and plan.preview.chance >= 80

def test_ordinary_bosses_follow_server_attempts_and_type_rules():
    selected = engine.ordinary_bosses(entries)
    assert all(b.type == "map" or (b.type == "world" and b.ordinary_attempts is not None) for b in selected)
    assert all(b.attempts > 0 and b.blocked_reason is None for b in selected)
    assert all(b.best_normal_preview.predicted_win and b.best_normal_preview.chance >= 80 for b in selected)

def test_world_windows_and_assist_attempts_are_exact():
    assert world_window(datetime(2026, 8, 17, 10, 0, tzinfo=SHANGHAI))
    assert not world_window(datetime(2026, 8, 17, 11, 0, tzinfo=SHANGHAI))
    assert world_plan.attempts == 3 and world_plan.endpoint == "/api/boss/assist"

def test_specialization_is_preserved_and_safe_rewards_exclude_choices():
    assert profession_plan.selected_profession_key == "cooking"
    assert all(reward.choice_count == 0 and reward.cost == 0 for reward in safe_rewards)
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/unit/test_policy.py -q`

Expected: FAIL with missing policy models and optimizer.

- [ ] **Step 3: Implement strict policy validation and bounded optimizer**

```python
# src/placegame/policy/models.py
class AccountPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idle_threshold_minutes: int = Field(690, ge=60)
    boss_min_chance: int = Field(80, ge=50, le=98)
    personal_paid_attempts: bool = False
    world_collaboration_enabled: bool = True
    world_attempts: Literal[3] = 3
    material_reserve: int = Field(64, ge=0)
    profession_food_target: int = Field(6, ge=0)
    profession_potion_target: int = Field(12, ge=0)
    profession_horizon_hours: int = Field(12, ge=1)
    inventory_warning_percent: int = Field(85, ge=1, le=99)
    inventory_critical_percent: int = Field(95, ge=1, le=100)
    inventory_auto_quality_ceiling: Literal["white", "green", "blue"] = "blue"
    inventory_keep_item_ids: frozenset[str] = frozenset()
    inventory_protected_affixes: frozenset[str] = frozenset()
    warehouse_auto_deposit_types: frozenset[str] = frozenset({"boss_material", "profession_material"})
    safe_reward_claims: bool = True

    @model_validator(mode="after")
    def ordered_inventory_thresholds(self):
        if self.inventory_critical_percent < self.inventory_warning_percent:
            raise ValueError("critical inventory threshold must be >= warning threshold")
        return self

class VersionedPolicy(AccountPolicy):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(ge=1)

class PolicyService(Protocol):
    async def get(self, account_id: UUID) -> VersionedPolicy: ...
    async def save(self, account_id: UUID, policy: AccountPolicy, expected_version: int, *, actor: Actor) -> VersionedPolicy: ...
    async def server_idle_capacity(self, account_id: UUID) -> int: ...
```

`BossOptimizer.optimize` generates exactly output/survival/balanced skill candidates (at most three skills), previews them with `none/assault/guard/focus` and no reward affix for at most 12 baseline previews, keeps three by predicted result/chance/remaining-player-HP/boss-HP, and tests affixes descending multiplier for at most 12 additional previews. It then selects the highest multiplier satisfying `predictedWin` and the policy chance. Cache keys contain boss, difficulty, equipment fingerprint, skill fingerprint, active potion, and combat-balance version; one fresh preview revalidates a cache hit. It considers `useMaterialBoost` only after combat selection, only on hard/nightmare, retains 64 material, and chooses the lowest-score equipped eligible slot. Potions are skipped on easy fights; otherwise selection is bottleneck-based, reserve-aware, and followed by a final preview.

`PolicyEngine` encodes idle emergency retries at 30/60/120 seconds, personal bosses from highest required level downward with nightmare → hard → normal ordering and the five-free-attempt pool, and ordinary bosses selected from type `map` plus type `world` entries exposing ordinary solo attempts. After every personal challenge outcome it re-reads the shared pool before considering another attempt. It trusts server `attempts`, `blockedReason`, difficulty availability, and refresh keys. Profession maintenance settles every five minutes, refills below two entries or six executable hours, never exceeds five entries, plans twelve hours when materials permit, and prioritizes unlock milestones → configured stock (six food, twelve of each potion) → inputs without a select operation. Safe claims are completed quests, daily activity, achievements, codex, and individual mail only when there is no choice, cost, or overflow; the executor dispatches the plan's typed claim kind to the matching `GameApi` method and never uses claim-all. `ActionPlan` stores a fingerprint, policy version, costs, risk class, five-minute expiry, and a structured `selected|skipped|blocked` decision plus reason for every evaluated action.

- [ ] **Step 4: Run policy tests and verify they pass**

Run: `uv run pytest tests/unit/test_policy.py -q`

Expected: all policy, ordering, window, reserve, and specialization tests pass.

- [ ] **Step 5: Commit the policy checkpoint**

```bash
git add src/placegame/policy src/placegame/boss_optimizer.py src/placegame/rewards.py tests/unit/test_policy.py
git commit -m "feat: add policy plans and boss optimizer"
```

### Task 6: Build Durable Jobs and the Beijing-Time Scheduler

**Files:**
- Create: `src/placegame/jobs/clock.py`
- Create: `src/placegame/jobs/store.py`
- Create: `src/placegame/jobs/handlers.py`
- Create: `src/placegame/jobs/scheduler.py`
- Modify: `src/placegame/models.py`
- Test: `tests/unit/test_scheduler.py`
- Test: `tests/fakes/clock.py`

**Interfaces:**
- Produces `Clock.now()`, the `JobStore` contract below, `Scheduler.tick()`, and handlers `run_idle`, `run_personal_bosses`, `run_map_bosses`, `run_world_window`, `run_professions`, `run_safe_rewards`.

```python
class JobStore(Protocol):
    async def acquire_scheduler_lease(self, worker_id: str, lease_seconds: int) -> bool: ...
    async def claim_due(self, now: datetime, limit: int) -> tuple[JobRun, ...]: ...
    async def finish_run(self, run_id: UUID, status: JobRunStatus, result: dict) -> None: ...
    async def retry_in(self, account_id: UUID, kind: JobKind, delay: timedelta) -> None: ...
    async def count_runs(self, *, idempotency_key: str) -> int: ...
    async def lease_status(self) -> SchedulerLeaseStatus: ...
```

- [ ] **Step 1: Write failing scheduler tests**

```python
async def test_world_window_wakes_15_seconds_early_and_attempts_each_instance(fake_clock, scheduler, fake_game):
    fake_clock.set("2026-08-17T09:59:45+08:00")
    await scheduler.tick()
    assert fake_game.poll_count == 1
    fake_game.world_instances = [WorldInstance("a", active=True, remaining_attempt_count=3)]
    fake_clock.advance(seconds=1)
    await scheduler.tick()
    assert fake_game.assist_calls == [("a", 1)]
    fake_clock.advance(seconds=1)
    await scheduler.tick()
    fake_clock.advance(seconds=1)
    await scheduler.tick()
    assert fake_game.assist_calls == [("a", 1), ("a", 2), ("a", 3)]

async def test_scheduler_lease_and_idempotency_prevent_duplicate_dispatch(two_schedulers, job_store):
    assert sum([await scheduler.tick() for scheduler in two_schedulers]) == 1
    assert await job_store.count_runs(idempotency_key="idle:acct:2026-08-17") == 1

async def test_world_jobs_have_priority_and_account_failures_are_isolated(scheduler, accounts):
    await scheduler.tick()
    assert scheduler.dispatch_order[0].kind == "world_boss"
    assert accounts[1].last_success_at is not None
```

- [ ] **Step 2: Run tests and verify missing job store/scheduler failure**

Run: `uv run pytest tests/unit/test_scheduler.py -q`

Expected: FAIL because `Scheduler` and `JobStore` are not defined.

- [ ] **Step 3: Implement lease, idempotency, misfire, and handlers**

```python
# src/placegame/jobs/clock.py
from datetime import datetime
from zoneinfo import ZoneInfo
SHANGHAI = ZoneInfo("Asia/Shanghai")
class Clock(Protocol):
    def now(self) -> datetime: ...

# src/placegame/jobs/scheduler.py
async def tick(self) -> int:
    if not await self.store.acquire_scheduler_lease(self.worker_id, self.settings.scheduler_lease_seconds):
        return 0
    due = await self.store.claim_due(self.clock.now(), limit=64)
    async def dispatch_limited(run: JobRun) -> None:
        async with self._account_slots:
            await self._dispatch(run)
    world = sorted((run for run in due if run.kind == "world_boss"), key=lambda run: run.run_at)
    ordinary = sorted((run for run in due if run.kind != "world_boss"), key=lambda run: run.run_at)
    await asyncio.gather(*(dispatch_limited(run) for run in world), return_exceptions=True)
    await asyncio.gather(*(dispatch_limited(run) for run in ordinary), return_exceptions=True)
    return len(due)
```

`self._account_slots` is a shared semaphore initialized to the configured default of four. `JobStore` records a logical job, run lease owner, attempt count, next retry, and unique `(account_id, idempotency_key)`. `claim_due` uses `FOR UPDATE SKIP LOCKED`; `finish_run` is idempotent. Misfires are replayed only when the job policy allows it. Idle summary and profession settlement run every five minutes; ordinary boss state runs every minute; personal bosses run after Beijing reset while free attempts remain. The world handler wakes 15 seconds before each Beijing window, polls once per second until five minutes after opening, skips locked/defeated/ended instances with an audited reason, calls `boss_assist` for every unlocked active still-alive instance, and verifies `myAttemptCount`/`remainingAttemptCount` before each of at most three attempts. Idle, personal, map, profession, and safe-reward handlers call `AccountService.mutate` and inventory-pressure planning hooks; no handler bypasses policy or locks.

- [ ] **Step 4: Run scheduler tests and verify they pass**

Run: `uv run pytest tests/unit/test_scheduler.py -q`

Expected: all lease, priority, world-window, retry, misfire, and isolation tests pass.

- [ ] **Step 5: Commit the scheduler checkpoint**

```bash
git add src/placegame/jobs src/placegame/models.py tests/unit/test_scheduler.py tests/fakes/clock.py
git commit -m "feat: add durable beijing-time scheduler"
```

### Task 7: Expose the Scoped Streamable HTTP MCP Server

**Files:**
- Create: `src/placegame/mcp/auth.py`
- Create: `src/placegame/mcp/context.py`
- Create: `src/placegame/mcp/server.py`
- Create: `src/placegame/mcp/tools.py`
- Modify: `src/placegame/app.py`
- Test: `tests/unit/test_mcp.py`

**Interfaces:**
- Produces `McpTokenService.list_metadata/get_metadata/create/rotate/verify/revoke`, `resolve_target`, and handlers for the non-inventory tools from the design: `accounts_list`, `account_status`, `idle_preview`, `bosses_list`, `boss_optimize`, `professions_status`, `jobs_list`, `audit_logs`, `idle_collect`, `boss_run_cycle`, `world_boss_participate`, `professions_maintain`, `rewards_claim_safe`, `automation_status`, `automation_pause`, `automation_resume`, `automation_run_now`, `policy_get`, and `policy_update`. `TOOL_SCOPES` also reserves the canonical scope mappings for `inventory_list`, `inventory_cleanup_plan`, `inventory_cleanup_execute`, and `warehouse_transfer`; those four handlers are registered and advertised only by Inventory Task 6.

```python
class McpTokenService(Protocol):
    async def list_metadata(self, *, actor: Actor) -> tuple[McpTokenMetadata, ...]: ...
    async def get_metadata(self, token_id: UUID, *, actor: Actor) -> McpTokenMetadata: ...
    async def create(self, name: str, expires_at: datetime, scopes: frozenset[str], allowed_account_ids: frozenset[UUID] | None, *, actor: Actor) -> IssuedMcpToken: ...
    async def rotate(self, token_id: UUID, *, actor: Actor) -> IssuedMcpToken: ...
    async def verify(self, secret: str, required_scope: str) -> VerifiedMcpCaller: ...
    async def revoke(self, token_id: UUID, *, actor: Actor) -> None: ...

class McpTokenStore(Protocol):
    async def find_by_digest(self, digest: str) -> McpTokenRecord | None: ...
```

```python
TOOL_SCOPES = {
    **dict.fromkeys(("accounts_list", "account_status", "idle_preview", "bosses_list", "boss_optimize", "professions_status", "inventory_list", "jobs_list", "audit_logs"), "game:read"),
    **dict.fromkeys(("idle_collect", "boss_run_cycle", "world_boss_participate", "professions_maintain", "inventory_cleanup_plan", "inventory_cleanup_execute", "warehouse_transfer", "rewards_claim_safe"), "game:operate"),
    **dict.fromkeys(("automation_status", "automation_pause", "automation_resume", "automation_run_now", "policy_get", "policy_update"), "automation:manage"),
}
```

`TOOL_SCOPES` is an authorization registry, not the advertised tool registry. Before the inventory extension is installed, `list_tools` omits all four reserved inventory names and direct calls return `tool_not_found`; the inventory plan adds their handlers without changing their canonical scopes.

The `admin` scope is reserved but has no MCP credential-management or token-management tools in the first release.

- [ ] **Step 1: Write failing protocol and authorization tests**

```python
async def test_token_scope_and_account_allowlist_are_enforced(mcp_client, token_for_account_a):
    read = await mcp_client.call("account_status", {"account_id": str(A_ID)}, token=token_for_account_a)
    assert read.is_error is False
    denied = await mcp_client.call("account_status", {"account_id": str(B_ID)}, token=token_for_account_a)
    assert denied.error.code == "account_not_allowed"
    denied_write = await mcp_client.call("idle_collect", {"account_id": str(A_ID)}, token=token_for_account_a)
    assert denied_write.error.code == "scope_required"

async def test_selector_requires_exactly_one_and_outputs_are_sanitized(mcp_client, operate_token):
    result = await mcp_client.call("accounts_list", {"account_id": str(A_ID), "all_enabled": True}, token=operate_token)
    assert result.error.code == "invalid_target"
    assert "session_token" not in json.dumps((await mcp_client.call("account_status", {"account_id": str(A_ID)}, token=operate_token)).json)

async def test_inventory_names_are_reserved_but_not_advertised_before_extension(mcp_client, read_token):
    names = {tool.name for tool in await mcp_client.list_tools(token=read_token)}
    assert names.isdisjoint({"inventory_list", "inventory_cleanup_plan", "inventory_cleanup_execute", "warehouse_transfer"})

async def test_batch_mutation_reports_partial_failure_per_account(mcp_client, operate_token, fake_game):
    fake_game.fail_account(B_ID, operation="idle_collect")
    result = await mcp_client.call("idle_collect", {"account_ids": [str(A_ID), str(B_ID)]}, token=operate_token)
    assert result.json["accounts"][str(A_ID)]["status"] == "succeeded"
    assert result.json["accounts"][str(B_ID)]["status"] == "failed"
    assert fake_game.idle_collect_count(A_ID) == 1
```

- [ ] **Step 2: Run MCP tests and verify they fail**

Run: `uv run pytest tests/unit/test_mcp.py -q`

Expected: FAIL because the token verifier and registered tools do not exist.

- [ ] **Step 3: Implement token auth, target resolution, and FastMCP adapter**

```python
# src/placegame/mcp/auth.py
async def verify_bearer(authorization: str | None, required_scope: str, store: McpTokenStore) -> VerifiedMcpCaller:
    if not authorization or not authorization.startswith("Bearer "):
        raise McpAuthError("missing_token")
    record = await store.find_by_digest(token_digest(authorization[7:]))
    if record is None or record.revoked_at or record.expires_at <= utcnow():
        raise McpAuthError("invalid_token")
    if required_scope not in record.scopes:
        raise McpAuthError("scope_required")
    return VerifiedMcpCaller(record.id, frozenset(record.scopes), frozenset(record.allowed_account_ids))

def authorize_accounts(caller: VerifiedMcpCaller, account_ids: tuple[UUID, ...]) -> Actor:
    if caller.allowed_account_ids and any(account_id not in caller.allowed_account_ids for account_id in account_ids):
        raise McpAuthError("account_not_allowed")
    return Actor("mcp", str(caller.token_id), caller.scopes)
```

`McpTokenService.create` generates a 256-bit URL-safe secret with a `pgm_` prefix, returns it once, and persists only prefix/digest/scopes/allowlist/expiry; `verify` updates last-used metadata and `revoke` takes effect before the next tool call. Use `FastMCP("placegame")` from the pinned SDK and mount `mcp.streamable_http_app()` at `/mcp`. Each advertised core tool parses a Pydantic input model, calls `AccountTarget.validate`, resolves account IDs, checks scope/allowlist through `authorize_accounts`, delegates to the policy/account service, and returns a structured result plus correlation ID. Keep handler registration separate from `TOOL_SCOPES` so extensions can add handlers explicitly. `all_enabled` is resolved server-side; batch mutations return per-account success/failure without rollback. The adapter has no generic HTTP tool and serializes through `redact` before returning.

- [ ] **Step 4: Run MCP tests and protocol smoke test**

Run: `uv run pytest tests/unit/test_mcp.py -q`

Expected: unit tests pass, including an initialize/request cycle over `/mcp` that receives a JSON structured tool result and a pre-inventory `list_tools` response with no inventory handlers.

- [ ] **Step 5: Commit the MCP checkpoint**

```bash
git add src/placegame/mcp src/placegame/app.py tests/unit/test_mcp.py
git commit -m "feat: expose scoped streamable http mcp"
```

### Task 8: Add Observability, Health Checks, and Core Acceptance Tests

**Files:**
- Create: `src/placegame/audit.py`
- Create: `src/placegame/observability.py`
- Create: `src/placegame/health.py`
- Modify: `src/placegame/app.py`
- Create: `tests/integration/test_core_acceptance.py`

**Interfaces:**
- Produces `/health/live`, `/health/ready`, structured JSON logs, 90-day audit cleanup, and the health contract consumed by the deployment plan.

- [ ] **Step 1: Write failing acceptance checks**

```python
async def test_ten_accounts_are_isolated_and_ambiguous_mutation_is_not_repeated(core_stack):
    accounts = await core_stack.seed_accounts(10)
    core_stack.game.commit_then_timeout("idle_collect", account_id=accounts[0].id)
    outcome = await core_stack.run_idle(accounts[0].id)
    assert outcome.applied is True
    assert core_stack.game.mutation_count("idle_collect", accounts[0].id) == 1
    snapshots = [await core_stack.snapshot(account.id) for account in accounts]
    assert all(snapshots)

async def test_readiness_reports_database_and_scheduler(core_stack):
    response = await core_stack.client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["scheduler_lease"]["healthy"] is True
```

- [ ] **Step 2: Run acceptance tests and verify they fail**

Run: `uv run pytest tests/integration/test_core_acceptance.py -q`

Expected: FAIL until health, audit, and readiness dependencies are wired.

- [ ] **Step 3: Implement redacted JSON logging and health**

```python
# src/placegame/health.py
@router.get("/health/ready")
async def ready(db: AsyncSession = Depends(get_session), scheduler: Scheduler = Depends(get_scheduler)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "scheduler_lease": await scheduler.lease_status()}
```

`audit.py` writes append-only events with actor/source/account/plan/action/cost/result/correlation ID and a scheduled 90-day purge that keeps metadata needed for tombstones. `observability.py` installs structlog processors that recursively redact secret keys, attaches correlation/account IDs, and records counters for job success/retries/missed windows, API latency, authentication failures, and inventory pressure. Health probes cover process, database, scheduler lease, and a non-mutating game-connectivity check. Alerts cover paused accounts, token expiry, world-boss misses, repeated failures, HTTP 426/schema protection, and unsafe inventory pressure through an adapter interface that can later support email, Telegram, or webhooks. `/health/ready` returns a stable sanitized scheduler-lease object so the deployment plan can gate rollout without seeing credentials or raw database errors.

- [ ] **Step 4: Run all core checks**

Run: `uv run pytest -q && uv run pyright src tests`

Expected: all core tests pass, migrations finish, and Pyright reports zero errors.

- [ ] **Step 5: Commit the core baseline**

```bash
git add src/placegame/audit.py src/placegame/observability.py src/placegame/health.py src/placegame/app.py tests/integration/test_core_acceptance.py
git commit -m "feat: add core observability and health"
```

## Core Self-Review Checklist

- Spec coverage: tasks 1–2 cover persistence, credential security, redaction, and audit; tasks 3–4 cover the typed API boundary, session renewal, locks, reconciliation, and account isolation; task 5 covers every default policy, optimizer bound, reserve, profession protection, and safe reward rule; task 6 covers all recurring jobs, Beijing windows, leases, retries, and concurrency; task 7 covers every core MCP handler plus canonical inventory scope reservations, selectors, and secret-redaction rules; task 8 covers health, observability, and core acceptance criteria. Inventory Task 6 completes and advertises the reserved inventory tools; the deployment plan covers image and server topology.
- Placeholder scan command: `rg -n -i "T[O]DO|T[B]D|F[I]XME|implement[ ]later|fill[ ]in|write[ ]tests[ ]for[ ]the[ ]above|appropriate[ ]error[ ]handling|similar[ ]to[ ]task" docs/superpowers/plans/2026-08-17-placegame-mcp-core.md`; expected output is empty.
- Type/signature check: `uv run pyright src tests` must report zero errors; `GameApi`, all frozen `AccountService` methods, `PlanStore`, `PolicyService`, `InventorySafetyPort`, `McpTokenService`, `Actor`, and `AccountTarget` must match the shared contracts above.
- Fresh verification: `uv run pytest -q` (including the Testcontainers migration check) and `uv run pyright src tests` must both succeed before moving to Inventory.
