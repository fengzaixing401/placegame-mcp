# PlaceGame P0-P1 Idle Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a clean development baseline and deliver transport-independent account status, atomic idle previews, and crash-safe idle collection on the retained core.

**Architecture:** Work in a new clean worktree from the final documentation commit. Preserve the committed account, crypto, typed game client, policy, and plan code, then add small application use cases. Preview persistence writes its optional plan and audit atomically. Execution persists a durable claim before game I/O and holds a separate PostgreSQL session advisory guard, so recovery after process exit can reconcile but can never resend.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy asyncio, PostgreSQL 16, HTTPX, pytest, Pyright.

## Global Constraints

- Planning and review are owned by `gpt-5.6-sol`; implementation is owned by `gpt-5.6-terra` or Luna.
- Preserve `D:\Ai\placegame-mcp\.worktrees\placegame-automation` exactly as-is. Its nine dirty Task 5C files are user-owned WIP.
- Implement only in `D:\Ai\placegame-mcp\.worktrees\placegame-idle-v1` on branch `feat/placegame-idle-v1`.
- Do not add MCP tools, admin routes, WebUI, scheduler behavior, boss behavior, profession behavior, rewards, or inventory behavior.
- Do not accept a caller-supplied game URL, HTTP method, headers, or arbitrary request body.
- A post-send timeout or process exit is never handled by resending the mutation.
- Fast tests run without Docker. PostgreSQL tests are marked `integration` and either run or skip explicitly.
- Production Python source passes Pyright with zero errors.
- Each task is test-first, independently reviewable, and ends in one commit.

## File Map

**Create:**

- `src/placegame/application/__init__.py`: public application exports.
- `src/placegame/application/errors.py`: stable application error-code mapping.
- `src/placegame/application/models.py`: strict status and idle result models.
- `src/placegame/application/status.py`: account list and status query.
- `src/placegame/application/idle.py`: idle planner, ports, preview, execute, and recovery use cases.
- `tests/fixtures/game/v1/{bootstrap,idle-summary,idle-collect}.json`: synthetic, provenance-marked fixtures.
- `tests/contract/test_idle_contract_fixtures.py`: schema, provenance, and redaction validation.
- `docs/contracts/placegame-idle-contract-status.md`: live-contract gate.
- `tests/unit/test_application_status.py`: fast status tests.
- `tests/unit/test_application_idle.py`: fast planner and orchestration tests.
- `tests/integration/test_idle_application.py`: PostgreSQL idle slice tests.
- `migrations/versions/003_action_plan_execution_claim.py`: durable execution-claim fields.

**Modify:**

- `pyproject.toml`, `tests/conftest.py`: test boundary.
- `src/placegame/config.py`: typed environment construction.
- `src/placegame/contracts.py`: bounded audit actor identity.
- `src/placegame/security/redaction.py`, `src/placegame/models.py`, `src/placegame/accounts/repository.py`: audit safety.
- `src/placegame/accounts/locks.py`: independent session-level execution guard.
- `src/placegame/accounts/service.py`: actor-aware locked context and claimed mutation path.
- `src/placegame/policy/plans.py`: atomic preview store and execution claims.
- `src/placegame/policy/engine.py`: delegate only idle planning semantics to the new planner.
- `src/placegame/db.py`, `src/placegame/app.py`: owned resources, composition, readiness.
- Focused existing tests affected by these contracts.

---

### Task 1: Create a Clean, Fast, Typed Baseline

**Files:**

- Modify: `pyproject.toml`
- Modify: `tests/conftest.py`
- Modify: `src/placegame/config.py`

**Interfaces:**

- Produces: clean branch `feat/placegame-idle-v1` in a separate worktree.
- Produces: `Settings.from_env() -> Settings` with no Pyright constructor error.
- Produces: `integration` marker for every test whose fixture graph contains `postgres_url`.

- [ ] **Step 1: Verify isolation and create the worktree**

Run from `D:\Ai\placegame-mcp`:

```powershell
$repo = 'D:\Ai\placegame-mcp'
$dirty = 'D:\Ai\placegame-mcp\.worktrees\placegame-automation'
$target = 'D:\Ai\placegame-mcp\.worktrees\placegame-idle-v1'
git -C $dirty status --short
git -C $repo check-ignore .worktrees
$base = git -C $repo rev-parse feat/placegame-automation
git -C $repo worktree add $target -b feat/placegame-idle-v1 $base
git -C $target status --short --branch
```

Expected: `.worktrees` is ignored; the original worktree still lists its nine dirty files; the new worktree is clean. If the path or branch exists, stop and inspect it. Do not delete or reuse it blindly.

- [ ] **Step 2: Install and capture the clean baseline**

Run in the new worktree:

```powershell
uv sync --frozen
.\.venv\Scripts\python.exe -m pyright src/placegame
```

Expected: the four dirty Task 5C `policy/engine.py` errors are absent. The only accepted source error is the known `Settings.from_env()` alias-generated call signature. Any other error means the worktree was created from the wrong state or exposes a separate baseline defect; stop and return it to sol.

- [ ] **Step 3: Register database tests without hiding them**

Keep the existing pytest settings and add:

```toml
markers = [
  "integration: requires PostgreSQL or another explicit external service",
]
```

Add to `tests/conftest.py`:

```python
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "postgres_url" in item.fixturenames:
            item.add_marker(pytest.mark.integration)
```

Keep `PLACEGAME_TEST_DATABASE_URL` as the preferred integration path. Before Testcontainers startup, probe Docker and skip only infrastructure absence:

```python
try:
    from docker import from_env
    from docker.errors import DockerException

    docker_client = from_env()
    try:
        docker_client.ping()
    finally:
        docker_client.close()
except DockerException:
    pytest.skip(
        "PostgreSQL integration test requires "
        "PLACEGAME_TEST_DATABASE_URL or a running Docker daemon"
    )
```

- [ ] **Step 4: Fix the settings type error**

Change only `Settings.from_env`:

```python
@classmethod
def from_env(cls) -> "Settings":
    return cls.model_validate({})
```

- [ ] **Step 5: Verify and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration" -q
.\.venv\Scripts\python.exe -m pytest --collect-only -m integration -q
.\.venv\Scripts\python.exe -m pyright src/placegame
git diff --check
```

Expected: fast tests pass without Docker, database tests collect as `integration`, Pyright reports zero errors, and the diff check is clean.

```powershell
git add pyproject.toml tests/conftest.py src/placegame/config.py
git commit -m "test: establish clean typed baseline"
```

---

### Task 2: Establish Synthetic Fixtures and a Live Contract Gate

**Files:**

- Create: `tests/fixtures/game/v1/bootstrap.json`
- Create: `tests/fixtures/game/v1/idle-summary.json`
- Create: `tests/fixtures/game/v1/idle-collect.json`
- Create: `tests/contract/test_idle_contract_fixtures.py`
- Create: `docs/contracts/placegame-idle-contract-status.md`

**Interfaces:**

- Consumes: `BootstrapState`, `IdleSummary`, `IdleCollectResult`.
- Produces: synthetic fixtures that are explicitly not live evidence.
- Produces: `live_contract_unverified`, which blocks a real P2 mutation surface.

- [ ] **Step 1: Write the failing provenance and schema test**

Create `tests/contract/test_idle_contract_fixtures.py`:

```python
import json
from pathlib import Path

import pytest

from placegame.game.schemas import BootstrapState, IdleCollectResult, IdleSummary
from placegame.security.redaction import redact

FIXTURES = Path(__file__).parents[1] / "fixtures" / "game" / "v1"


@pytest.mark.parametrize(
    ("filename", "endpoint", "schema"),
    [
        ("bootstrap.json", "GET /api/client/bootstrap", BootstrapState),
        ("idle-summary.json", "GET /api/client/idle-summary", IdleSummary),
        ("idle-collect.json", "POST /api/battle/idle-collect", IdleCollectResult),
    ],
)
def test_synthetic_fixture_is_redacted_versioned_and_strict(
    filename, endpoint, schema
):
    document = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    metadata = document["_fixture"]
    assert redact(document) == document
    assert metadata == {
        "provenance": "synthetic",
        "endpoint": endpoint,
        "createdAt": "2026-08-19",
        "verifiedAt": None,
        "gameContractVersion": "unverified",
        "redactionMethod": "placegame.security.redaction.redact",
        "liveContractStatus": "unverified",
    }
    schema.model_validate(document["data"])
```

Run it and expect missing-file failures.

- [ ] **Step 2: Add exact synthetic fixtures**

Each file has the metadata asserted above. Its `data` value is respectively:

```json
{"accountId": "fixture-account-001"}
```

```json
{"accumulatedSeconds": 41400, "capacitySeconds": 43200}
```

```json
{"collected": true}
```

Do not label these files captured, observed, or verified.

- [ ] **Step 3: Record the publication gate**

Create `docs/contracts/placegame-idle-contract-status.md`:

```markdown
# PlaceGame Idle Contract Status

- Status: `live_contract_unverified`
- Synthetic fixture date: `2026-08-19`
- Endpoints: `GET /api/client/bootstrap`, `GET /api/client/idle-summary`, `POST /api/battle/idle-collect`
- Redaction: apply `placegame.security.redaction.redact`, then manually verify that credentials, authorization headers, cookies, session tokens, and unrelated account data are absent.
- Publication gate: P2 may test protocol plumbing against the fake server, but must not expose live `idle_collect` until an opt-in credentialed capture is redacted, schema-validated, reviewed, and this status is changed in a dedicated contract-verification commit.
```

- [ ] **Step 4: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contract/test_idle_contract_fixtures.py tests/unit/test_game_client.py -q
git diff --check
git add tests/fixtures/game/v1 tests/contract/test_idle_contract_fixtures.py docs/contracts/placegame-idle-contract-status.md
git commit -m "test: define idle contract verification gate"
```

Expected: fixtures pass strict schemas, recursive redaction is idempotent, and status remains unverified.

---

### Task 3: Make Audit Persistence Safe and Typed

**Files:**

- Modify: `src/placegame/models.py`
- Modify: `src/placegame/contracts.py`
- Modify: `src/placegame/accounts/repository.py`
- Modify: `src/placegame/accounts/service.py`
- Modify: `tests/unit/test_security.py`
- Modify: `tests/unit/test_accounts.py`

**Interfaces:**

- Produces: all `AuditEvent` JSON fields use `RedactedJSON`.
- Produces: `audit_identity(actor: Actor) -> tuple[str, ActorKind]`.
- Produces: exact `AccountRepository.add_audit` arguments for costs and correlation.

- [ ] **Step 1: Write failing boundary tests**

Add to `tests/unit/test_security.py`:

```python
def test_every_structured_audit_column_uses_redacted_json():
    names = {"costs", "result", "before", "after"}
    columns = {
        column.name: column.type
        for column in AuditEvent.__table__.columns
        if column.name in names
    }
    assert set(columns) == names
    assert all(isinstance(value, RedactedJSON) for value in columns.values())


def test_audit_identity_is_ascii_bounded_and_keeps_source():
    actor, source = audit_identity(Actor("mcp", "用户/token\nname"))
    assert actor == "mcp:___token_name"
    assert source == "mcp"
    assert len(actor) <= 128
```

Add one integration regression that inserts nested `sessionToken`, `password`, `authorization`, and `cookie` values through `AccountRepository.add_audit`, reloads in a new session, and asserts every sensitive value is `[REDACTED]` in `costs`, `result`, `before`, and `after`.

- [ ] **Step 2: Put every audit JSON field behind the type decorator**

In `AuditEvent`:

```python
costs: Mapped[dict | None] = mapped_column(RedactedJSON(), nullable=True)
result: Mapped[dict | None] = mapped_column(RedactedJSON(), nullable=True)
before: Mapped[dict | None] = mapped_column(RedactedJSON(), nullable=True)
after: Mapped[dict | None] = mapped_column(RedactedJSON(), nullable=True)
```

This changes binding behavior, not PostgreSQL column shape; no migration is needed.

- [ ] **Step 3: Centralize actor formatting**

Add to `contracts.py`:

```python
def audit_identity(actor: Actor) -> tuple[str, ActorKind]:
    actor_id = "".join(
        character
        if character.isascii()
        and (character.isalnum() or character in "_.:@-")
        else "_"
        for character in actor.actor_id
    )
    return f"{actor.kind}:{actor_id}"[:128], actor.kind
```

Remove the private account-service formatter and use this function from all persistence adapters.

- [ ] **Step 4: Complete the repository signature**

Add exact optional parameters:

```python
costs: Mapping[str, Any] | None = None,
correlation_id: str | None = None,
```

Assign `costs`, `result`, `before`, `after`, and `correlation_id` to `AuditEvent`. Do not sanitize at individual callers; the ORM boundary remains mandatory.

- [ ] **Step 5: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_security.py -q
.\.venv\Scripts\python.exe -m pytest -m integration tests/unit/test_accounts.py -q
.\.venv\Scripts\python.exe -m pyright src/placegame/contracts.py src/placegame/accounts src/placegame/models.py
git diff --check
git add src/placegame/models.py src/placegame/contracts.py src/placegame/accounts/repository.py src/placegame/accounts/service.py tests/unit/test_security.py tests/unit/test_accounts.py
git commit -m "fix: redact all structured audit data"
```

Expected: fast tests pass; integration passes or skips explicitly; Pyright is clean.

---

### Task 4: Add the Shared Account Status Query

**Files:**

- Create: `src/placegame/application/__init__.py`
- Create: `src/placegame/application/errors.py`
- Create: `src/placegame/application/models.py`
- Create: `src/placegame/application/status.py`
- Create: `tests/unit/test_application_status.py`
- Modify: `src/placegame/accounts/repository.py`
- Modify: `src/placegame/accounts/service.py`
- Modify: `tests/unit/test_accounts.py`

**Interfaces:**

```python
class AccountGateway(Protocol):
    async def list(self) -> tuple[ManagedAccount, ...]: ...
    async def get(self, account_id: UUID) -> ManagedAccount: ...
    async def snapshot(
        self, account_id: UUID, *, actor: Actor
    ) -> AccountSnapshot: ...
```

- Produces: `AccountStatusQuery.list_accounts() -> tuple[AccountSummary, ...]`.
- Produces: `AccountStatusQuery.get_status(account_id, actor) -> AccountStatus`.
- List auth state is `required` or `unknown`; only an authoritative snapshot may return `authenticated`.

- [ ] **Step 1: Write failing fast tests**

Create stubs implementing the protocol, then add:

```python
async def test_list_does_not_guess_authentication():
    query = AccountStatusQuery(StubAccounts.two_accounts_out_of_order())
    result = await query.list_accounts()
    assert [item.label for item in result] == ["alpha", "zeta"]
    assert [item.auth_state for item in result] == ["unknown", "required"]


async def test_status_uses_authoritative_snapshot_and_contains_no_token():
    query = AccountStatusQuery(StubAccounts.one_authenticated_account())
    result = await query.get_status(
        ACCOUNT_ID, actor=Actor("webui", "admin")
    )
    assert result.account.auth_state == "authenticated"
    assert result.bootstrap_account_id == "game-account-001"
    assert result.idle.accumulated_seconds == 41400
    assert result.idle.capacity_seconds == 43200
    assert "token" not in result.model_dump_json().lower()
```

Run and expect import failure because the application package does not exist.

- [ ] **Step 2: Define strict application models**

Use `ConfigDict(extra="forbid", frozen=True)` on a shared base. Define:

```python
class AccountSummary(AppModel):
    account_id: UUID
    label: str
    enabled: bool
    paused_reason: str | None
    auth_state: Literal["authenticated", "required", "unknown"]


class IdleState(AppModel):
    accumulated_seconds: int
    capacity_seconds: int


class AccountStatus(AppModel):
    account: AccountSummary
    bootstrap_account_id: str
    idle: IdleState
    fetched_at: datetime


class IdlePreview(AppModel):
    account_id: UUID
    plan_id: UUID | None
    decision: Literal["collect", "wait"]
    accumulated_seconds: int
    capacity_seconds: int
    threshold_seconds: int
    expires_at: datetime | None
    reason: str
    correlation_id: str


class IdleExecution(AppModel):
    account_id: UUID
    plan_id: UUID
    status: Literal["executed", "reconciled"]
    applied: bool
    reconciled: bool
    collected: bool
    correlation_id: str
```

- [ ] **Step 3: Define stable error codes without parsing messages**

`ApplicationErrorCode` contains the design codes plus `plan_in_progress`. Map concrete exception classes only:

```python
def application_error_code(exc: Exception) -> ApplicationErrorCode:
    if isinstance(exc, AccountNotFound):
        return "account_not_found"
    if isinstance(exc, AccountDisabled):
        return "account_disabled"
    if isinstance(exc, AccountPaused):
        return "account_paused"
    if isinstance(exc, AuthenticationRequired):
        return "authentication_required"
    if isinstance(exc, PlanExecutionInProgress):
        return "plan_in_progress"
    if isinstance(exc, PlanPreconditionFailed):
        return "plan_not_executable"
    if isinstance(exc, (ContractChanged, GameSchemaMismatch)):
        return "game_contract_changed"
    if isinstance(exc, ReconciliationRequired):
        return "mutation_reconciliation_required"
    if isinstance(exc, GameError):
        return "game_temporarily_unavailable"
    return "internal_error"
```

Reserve `forbidden_account`, `plan_not_found`, `plan_expired`, and `plan_stale` for P2. Do not infer them from exception text. Add a parameterized test for every emitted mapping and the fallback.

- [ ] **Step 4: Add deterministic account listing and actor-aware locked context**

Repository:

```python
async def list(self, session: AsyncSession) -> tuple[GameAccount, ...]:
    rows = await session.scalars(
        select(GameAccount).order_by(GameAccount.label, GameAccount.id)
    )
    return tuple(rows)
```

Service:

```python
async def list(self) -> tuple[ManagedAccount, ...]:
    async with self.sessions() as session:
        rows = await self.repository.list(session)
        return tuple(self._managed(row) for row in rows)


def locked(
    self, account_id: UUID, *, actor: Actor
) -> AbstractAsyncContextManager[LockedAccount]:
    return self._locked(account_id, actor=actor)
```

Pass the supplied actor to `_ensure_locked`; update focused callers.

- [ ] **Step 5: Implement status projection**

`SnapshotState` is a private strict model with aliases `accountId`, `accumulatedSeconds`, and `capacitySeconds`. Validation failure becomes a safe `GameSchemaMismatch` with no invalid payload. Listing maps only explicit `authentication_required` to `required`; every other list item is `unknown`. `get_status` maps `snapshot.authenticated` to `authenticated` or `required`.

- [ ] **Step 6: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_application_status.py tests/unit/test_accounts.py -m "not integration" -q
.\.venv\Scripts\python.exe -m pyright src/placegame/application src/placegame/accounts
git diff --check
git add src/placegame/application src/placegame/accounts/repository.py src/placegame/accounts/service.py tests/unit/test_application_status.py tests/unit/test_accounts.py
git commit -m "feat: add shared account status query"
```

---

### Task 5: Persist Idle Previews Atomically

**Files:**

- Create: `src/placegame/application/idle.py`
- Create: `tests/unit/test_application_idle.py`
- Create: `tests/integration/test_idle_application.py`
- Modify: `src/placegame/policy/plans.py`
- Modify: `src/placegame/policy/engine.py`
- Modify: `tests/unit/test_policy_engine.py`

**Interfaces:**

```python
Clock = Callable[[], datetime]
CorrelationIdFactory = Callable[[], str]


class LockedAccountGateway(Protocol):
    def locked(
        self, account_id: UUID, *, actor: Actor
    ) -> AbstractAsyncContextManager[LockedAccount]: ...


class IdlePreviewStore(Protocol):
    async def persist(
        self,
        *,
        draft: ActionPlanDraft | None,
        actor: Actor,
        account_id: UUID,
        decision: Literal["collect", "wait"],
        reason: str,
        correlation_id: str,
    ) -> TypedActionPlan | None: ...
```

- Produces: `IdlePlanner.build(...) -> IdlePlanBuild`.
- Produces: eligibility fingerprint based on capacity and eligible boolean, never exact elapsed seconds.
- Produces: one transaction containing optional plan plus mandatory preview audit.

- [ ] **Step 1: Write failing planner and orchestration tests**

Add:

```python
def test_fingerprint_survives_natural_growth_after_threshold():
    policy = VersionedPolicy(version=1)
    first = IdleSummary(accumulatedSeconds=41400, capacitySeconds=43200)
    later = IdleSummary(accumulatedSeconds=42000, capacitySeconds=43200)
    assert IdlePlanner.fingerprint(policy, first) == IdlePlanner.fingerprint(
        policy, later
    )


def test_fingerprint_changes_after_external_collection():
    policy = VersionedPolicy(version=1)
    before = IdleSummary(accumulatedSeconds=41400, capacitySeconds=43200)
    after = IdleSummary(accumulatedSeconds=0, capacitySeconds=43200)
    assert IdlePlanner.fingerprint(policy, before) != IdlePlanner.fingerprint(
        policy, after
    )


async def test_wait_persists_audit_without_plan():
    store = StubIdlePreviewStore()
    use_case = IdlePlanUseCase(
        StubLockedAccounts(idle_seconds=60),
        store,
        fixed_clock,
        lambda: "corr-wait",
    )
    preview = await use_case.preview(ACCOUNT_ID, actor=Actor("mcp", "token-1"))
    assert preview.plan_id is None
    assert store.calls[0].draft is None
    assert store.calls[0].correlation_id == "corr-wait"


async def test_collect_persists_plan_and_matching_audit():
    store = StubIdlePreviewStore()
    use_case = IdlePlanUseCase(
        StubLockedAccounts(idle_seconds=41400),
        store,
        fixed_clock,
        lambda: "corr-collect",
    )
    preview = await use_case.preview(ACCOUNT_ID, actor=Actor("webui", "admin"))
    assert preview.plan_id == store.stored[0].id
    assert store.stored[0].family == "idle"
    assert preview.correlation_id == "corr-collect"
```

Run and expect missing-implementation failures.

- [ ] **Step 2: Implement the pure planner**

```python
@dataclass(frozen=True)
class IdlePlanBuild:
    draft: ActionPlanDraft
    summary: IdleSummary
    threshold_seconds: int
    decision: Literal["collect", "wait"]
    reason: str


class IdlePlanner:
    @staticmethod
    def threshold(policy: VersionedPolicy, summary: IdleSummary) -> int:
        return min(policy.idle_threshold_minutes * 60, summary.capacity_seconds)

    @classmethod
    def fingerprint(cls, policy: VersionedPolicy, summary: IdleSummary) -> str:
        threshold = cls.threshold(policy, summary)
        return canonical_fingerprint(
            "idle",
            {
                "capacitySeconds": summary.capacity_seconds,
                "eligible": summary.accumulated_seconds >= threshold,
            },
        )
```

`build` creates exactly one decision, `risk="low"`, `confirmation_required=False`, five-minute expiry, and `EstimatedCosts(0, 0, 0)`. The existing clean `PolicyEngine.build_idle_plan` delegates its idle threshold and fingerprint to this planner; do not port any dirty Task 5C state-source code.

- [ ] **Step 3: Implement atomic PostgreSQL preview persistence**

In `policy/plans.py`:

```python
class PostgresIdlePreviewStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        repository: AccountRepository | None = None,
    ) -> None:
        self.sessions = sessions
        self.repository = repository or AccountRepository()

    async def persist(
        self,
        *,
        draft: ActionPlanDraft | None,
        actor: Actor,
        account_id: UUID,
        decision: Literal["collect", "wait"],
        reason: str,
        correlation_id: str,
    ) -> TypedActionPlan | None:
        async with self.sessions.begin() as session:
            plan = (
                await PostgresPlanStore(session).create(draft)
                if draft is not None
                else None
            )
            actor_value, source = audit_identity(actor)
            await self.repository.add_audit(
                session,
                actor=actor_value,
                source=source,
                account_id=account_id,
                plan_id=None if plan is None else plan.id,
                action="idle.preview",
                result={"decision": decision, "reason": reason},
                correlation_id=correlation_id,
            )
            return plan
```

Add an integration test with a repository whose `add_audit` raises. Reload in a fresh session and assert the account has zero action plans. This proves audit failure rolls back plan creation.

- [ ] **Step 4: Implement typed preview orchestration**

```python
class IdlePlanUseCase:
    def __init__(
        self,
        accounts: LockedAccountGateway,
        previews: IdlePreviewStore,
        clock: Clock,
        correlation_ids: CorrelationIdFactory,
    ) -> None:
        self.accounts = accounts
        self.previews = previews
        self.clock = clock
        self.correlation_ids = correlation_ids

    async def preview(self, account_id: UUID, *, actor: Actor) -> IdlePreview:
        correlation_id = self.correlation_ids()
        async with self.accounts.locked(account_id, actor=actor) as locked:
            summary = await locked.api.idle_summary()
            build = IdlePlanner.build(
                account_id, locked.policy, summary, self.clock()
            )
        stored = await self.previews.persist(
            draft=build.draft if build.decision == "collect" else None,
            actor=actor,
            account_id=account_id,
            decision=build.decision,
            reason=build.reason,
            correlation_id=correlation_id,
        )
        return IdlePreview(
            account_id=account_id,
            plan_id=None if stored is None else stored.id,
            decision=build.decision,
            accumulated_seconds=summary.accumulated_seconds,
            capacity_seconds=summary.capacity_seconds,
            threshold_seconds=build.threshold_seconds,
            expires_at=None if stored is None else stored.expires_at,
            reason=build.reason,
            correlation_id=correlation_id,
        )
```

- [ ] **Step 5: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_application_idle.py tests/unit/test_policy_engine.py -m "not integration" -q
.\.venv\Scripts\python.exe -m pytest -m integration tests/integration/test_idle_application.py -q
.\.venv\Scripts\python.exe -m pyright src/placegame/application src/placegame/policy
git diff --check
git add src/placegame/application/idle.py src/placegame/policy/plans.py src/placegame/policy/engine.py tests/unit/test_application_idle.py tests/unit/test_policy_engine.py tests/integration/test_idle_application.py
git commit -m "feat: persist idle previews atomically"
```

---

### Task 6: Claim, Execute, and Recover Idle Collection Safely

**Files:**

- Create: `migrations/versions/003_action_plan_execution_claim.py`
- Modify: `src/placegame/models.py`
- Modify: `src/placegame/errors.py`
- Modify: `src/placegame/accounts/locks.py`
- Modify: `src/placegame/accounts/service.py`
- Modify: `src/placegame/policy/plans.py`
- Modify: `src/placegame/application/idle.py`
- Modify: `tests/unit/test_plans.py`
- Modify: `tests/unit/test_accounts.py`
- Modify: `tests/unit/test_application_idle.py`
- Modify: `tests/integration/test_idle_application.py`
- Modify: `tests/integration/test_migrations.py`

**Interfaces:**

```python
PlanGuard = Callable[[TypedActionPlan], bool]


class ExecutionGuard(Protocol):
    def hold(self, account_id: UUID) -> AbstractAsyncContextManager[None]: ...


class ExecutionClaims(Protocol):
    async def claim(
        self,
        *,
        plan_id: UUID,
        account_id: UUID,
        owner: str,
        actor: Actor,
        correlation_id: str,
        now: datetime,
        lease_expires_at: datetime,
        guard: PlanGuard,
    ) -> ExecutionClaim: ...

    async def finish_recovery(
        self,
        *,
        plan_id: UUID,
        owner: str,
        status: TerminalPlanState,
        result: ExecutionResult,
        actor: Actor,
        correlation_id: str,
    ) -> None: ...


class ClaimedMutationGateway(Protocol):
    async def mutate_claimed(
        self,
        account_id: UUID,
        operation: Callable[[GameApi], Awaitable[IdleCollectResult]],
        *,
        actor: Actor,
        plan_id: UUID,
        execution_owner: str,
        state_fingerprint: StateFingerprintResolver,
        verify: Callable[[GameApi, IdleCollectResult | None], Awaitable[bool]],
        correlation_id: str,
    ) -> MutationOutcome[IdleCollectResult]: ...


class IdleAccountGateway(
    LockedAccountGateway, ClaimedMutationGateway, Protocol
):
    pass


class PolicyReader(Protocol):
    async def get(self, account_id: UUID) -> VersionedPolicy: ...
```

`ExecutionClaim.mode` is `execute` or `recover`. The application never receives an untyped plan row or SQLAlchemy session.

The execution lease is fixed at two minutes for P0-P1:

```python
EXECUTION_LEASE = timedelta(minutes=2)
```

The use case passes `lease_expires_at=now + EXECUTION_LEASE` for initial and recovery ownership.

- [ ] **Step 1: Write RED tests for claims and exact idle shape**

Add tests proving `_is_idle_collect_plan` returns true only for:

```python
len(plan.decisions) == 1
and isinstance(plan.decisions[0], SelectedDecision)
and isinstance(plan.decisions[0].action, IdleCollectAction)
and plan.risk == "low"
and plan.confirmation_required is False
```

Add zero-mutation tests for a high-risk plan, confirmation-required plan, extra skipped decision, expired plan, cross-account plan, disabled account, and paused account.

Add store tests:

- first pending claim commits `executing`, owner, start, lease, and attempt count 1;
- a terminal plan cannot be claimed;
- an audit failure rolls the claim back to pending;
- an executing plan with an unexpired lease raises `PlanExecutionInProgress`, even when the caller acquired the session guard;
- an executing plan with an expired lease reached while holding the execution guard returns `mode="recover"` and transfers only recovery ownership; it never increments mutation attempt count.

Run focused tests and expect missing migration/model/store failures.

- [ ] **Step 2: Add durable claim fields**

Migration `003_action_plan_execution_claim` and `ActionPlan` add:

```python
execution_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
execution_started_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True), nullable=True
)
execution_lease_expires_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True), nullable=True
)
execution_attempt_count: Mapped[int] = mapped_column(
    Integer, nullable=False, default=0, server_default="0"
)
```

Add the same fields to `TypedActionPlan` with validation: pending/confirmed plans have no owner/timestamps; executing plans require all three; terminal plans allow either all-null legacy claim metadata or one complete retained claim, never a partial claim; attempt count is non-negative. Extend migration round-trip tests.

- [ ] **Step 3: Add the independent session execution guard**

Add `PlanExecutionInProgress(AccountError)` and implement seed namespace 2 in `accounts/locks.py`:

```python
class PostgresExecutionGuard:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @asynccontextmanager
    async def hold(self, account_id: UUID) -> AsyncIterator[None]:
        async with self.engine.connect() as connection:
            acquired = await connection.scalar(
                text(
                    "SELECT pg_try_advisory_lock("
                    "hashtextextended(:account_id, 2))"
                ),
                {"account_id": str(account_id)},
            )
            if acquired is not True:
                raise PlanExecutionInProgress() from None
            try:
                yield
            finally:
                await connection.execute(
                    text(
                        "SELECT pg_advisory_unlock("
                        "hashtextextended(:account_id, 2))"
                    ),
                    {"account_id": str(account_id)},
                )
```

This connection is separate from the normal seed-0 account transaction lock. It remains open across claim commit, game I/O, and terminal commit. PostgreSQL releases it if the process connection dies.

- [ ] **Step 4: Implement atomic claim or recovery ownership**

`PostgresExecutionClaims.claim` opens a short transaction, row-locks the plan by account, parses it as `TypedActionPlan`, and follows this closed transition:

```text
pending/confirmed + valid guard       -> executing, mode=execute, attempt += 1
executing + unexpired lease           -> PlanExecutionInProgress
executing + expired lease             -> executing, mode=recover, attempt unchanged
terminal or invalid                   -> PlanPreconditionFailed
```

For the execute transition, validate expiry, confirmation state, and the supplied exact plan guard. Recovery requires both the session guard and `execution_lease_expires_at <= now`; update owner and lease but never return `mode=execute`. Write `idle.execution.claimed` or `idle.execution.recovery_claimed` audit with the same correlation ID in the claim transaction. Audit failure rolls back the transition.

`finish_recovery` row-locks by plan and owner, permits only `executed` or `reconciliation_required`, terminalizes, and writes the recovery audit in one transaction.

- [ ] **Step 5: Add a claimed mutation path**

`AccountService.mutate_claimed` retains the existing account seed-0 transaction lock and timeout reconciliation behavior, but requires:

```python
async def mutate_claimed(
    self,
    account_id: UUID,
    operation: Callable[[GameApi], Awaitable[T]],
    *,
    actor: Actor,
    plan_id: UUID,
    execution_owner: str,
    state_fingerprint: StateFingerprintResolver,
    verify: Callable[[GameApi, T | None], Awaitable[bool]],
    correlation_id: str,
) -> MutationOutcome[T]:
```

- plan state is `executing`;
- `execution_owner` exactly matches;
- policy version and authoritative fingerprint still match;
- no game send occurs before those checks;
- terminal plan update requires the same owner;
- every audit receives the caller's bounded correlation ID.

Keep the legacy `mutate` path for already committed deferred code, but the Idle use case must never call it. Share private mechanics only when it reduces duplication without changing legacy behavior.

- [ ] **Step 6: Implement execute versus recovery**

`IdleExecuteUseCase.execute`:

1. generates correlation and owner IDs;
2. enters `execution_guard.hold(account_id)`;
3. calls `claims.claim` with `_is_idle_collect_plan`;
4. for `mode=execute`, loads policy, builds an eligibility resolver that captures the pre-send summary, and calls `mutate_claimed`;
5. for `mode=recover`, performs only an authoritative idle read through `accounts.locked`.

Its constructor is fully typed:

```python
class IdleExecuteUseCase:
    def __init__(
        self,
        *,
        accounts: IdleAccountGateway,
        policies: PolicyReader,
        execution_guard: ExecutionGuard,
        claims: ExecutionClaims,
        clock: Clock,
        owner_ids: Callable[[], str],
        correlation_ids: CorrelationIdFactory,
    ) -> None:
        self.accounts = accounts
        self.policies = policies
        self.execution_guard = execution_guard
        self.claims = claims
        self.clock = clock
        self.owner_ids = owner_ids
        self.correlation_ids = correlation_ids
```

Recovery logic is exact:

```python
summary = await locked.api.idle_summary()
threshold = IdlePlanner.threshold(locked.policy, summary)
if summary.accumulated_seconds < threshold:
    status = "executed"
    result = {"status": "succeeded", "reconciled": True}
else:
    status = "reconciliation_required"
    result = {"status": "ambiguous"}
await claims.finish_recovery(
    plan_id=plan_id,
    owner=owner,
    status=status,
    result=result,
    actor=actor,
    correlation_id=correlation_id,
)
```

Recovery never invokes `idle_collect`, even when the state is still eligible.

- [ ] **Step 7: Prove process-exit recovery sends once**

In the integration test define:

```python
class SimulatedProcessExit(BaseException):
    pass
```

Monkeypatch the claimed terminal write to raise `SimulatedProcessExit` after the fake game has committed. First execution must leave the separately committed plan in `executing` and mutation count 1. Restore terminal writes. An immediate retry must return `PlanExecutionInProgress`. Advance the controlled clock beyond `execution_lease_expires_at` and retry: it must acquire the released session guard, choose recovery, terminalize as reconciled, and keep mutation count exactly 1.

Add a second test that crashes before send after claim commit. Recovery sees the still-eligible state, returns/raises reconciliation-required, and mutation count remains zero. Add a concurrency test where the first holder pauses after claim; the second call receives `PlanExecutionInProgress` and cannot reconcile or terminalize.

- [ ] **Step 8: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_plans.py tests/unit/test_accounts.py tests/unit/test_application_idle.py -m "not integration" -q
.\.venv\Scripts\python.exe -m pytest -m integration tests/integration/test_idle_application.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py -q
.\.venv\Scripts\python.exe -m pyright src/placegame/application src/placegame/accounts src/placegame/policy/plans.py src/placegame/models.py
git diff --check
git add migrations/versions/003_action_plan_execution_claim.py src/placegame/models.py src/placegame/errors.py src/placegame/accounts/locks.py src/placegame/accounts/service.py src/placegame/policy/plans.py src/placegame/application/idle.py tests/unit/test_plans.py tests/unit/test_accounts.py tests/unit/test_application_idle.py tests/integration/test_idle_application.py tests/integration/test_migrations.py
git commit -m "feat: make idle execution crash safe"
```

---

### Task 7: Compose and Verify P0-P1

**Files:**

- Modify: `src/placegame/db.py`
- Modify: `src/placegame/app.py`
- Modify: `src/placegame/application/__init__.py`
- Modify: `tests/unit/test_app_bootstrap.py`

**Interfaces:**

```python
DatabaseFactory = Callable[[Settings], Database]
HttpClientFactory = Callable[[], httpx.AsyncClient]
```

- Produces: `Database(engine, sessions)` and `await Database.close()`.
- Produces: lifespan state `status_query`, `idle_plan`, `idle_execute`.
- Produces: `/health/live` and database-backed `/health/ready` only.

- [ ] **Step 1: Write failing lifecycle tests**

Inject fake database and HTTP factories into `create_app`. Test that:

- liveness succeeds even when readiness storage raises;
- readiness returns exactly `{"status": "ok"}` or HTTP 503 `{"detail": "not ready"}`;
- a database error containing a password is absent from the response;
- database and HTTP client each close exactly once;
- the three application services exist during lifespan.

- [ ] **Step 2: Make database ownership explicit**

```python
@dataclass(frozen=True)
class Database:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    async def close(self) -> None:
        await self.engine.dispose()


def create_database(settings: Settings) -> Database:
    engine = create_async_engine(settings.read_database_url(), pool_pre_ping=True)
    return Database(
        engine=engine,
        sessions=async_sessionmaker(engine, expire_on_commit=False),
    )
```

Keep `get_session` only as a compatibility wrapper for committed tests.

- [ ] **Step 3: Compose exact production adapters**

Inside lifespan create one database and one shared HTTP client. Compose:

```python
accounts: AccountService | None = None


async def read_idle_capacity(account_id: UUID) -> int:
    if accounts is None:
        raise PolicyUnavailable() from None
    async with accounts.locked(
        account_id, actor=Actor("scheduler", "policy-capacity")
    ) as locked:
        return (await locked.api.idle_summary()).capacity_seconds


policy = PostgresPolicyService(database.sessions, read_idle_capacity)
accounts = AccountService(
    database.sessions,
    secret_box,
    lambda token: HttpGameClient(
        settings, session_token=token, http_client=http_client
    ),
    policy_provider=policy,
)
previews = PostgresIdlePreviewStore(database.sessions)
execution_guard = PostgresExecutionGuard(database.engine)
claims = PostgresExecutionClaims(database.sessions)
status_query = AccountStatusQuery(accounts)
idle_plan = IdlePlanUseCase(
    accounts,
    previews,
    lambda: datetime.now(timezone.utc),
    lambda: uuid4().hex,
)
idle_execute = IdleExecuteUseCase(
    accounts=accounts,
    policies=policy,
    execution_guard=execution_guard,
    claims=claims,
    clock=lambda: datetime.now(timezone.utc),
    owner_ids=lambda: uuid4().hex,
    correlation_ids=lambda: uuid4().hex,
)
```

`PostgresPolicyService.get` does not call the capacity reader, so normal locked reads do not recurse.

- [ ] **Step 4: Add safe lifespan and readiness**

Assign only composed services and owned resources to `app.state`. Close HTTPX and database in `finally`. Readiness runs `select(1)` in a short session and never returns exception text. Do not add product routes.

- [ ] **Step 5: Run the complete gate once**

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration" -q
.\.venv\Scripts\python.exe -m pytest -m integration -q
.\.venv\Scripts\python.exe -m pyright src/placegame
git diff --check
git status --short --branch
```

Expected: fast tests pass without Docker; integration passes with an explicit database or Docker and otherwise skips cleanly; Pyright has zero errors; diff check is clean; only intended Task 7 files are dirty.

- [ ] **Step 6: Commit and hand off**

```powershell
git add src/placegame/db.py src/placegame/app.py src/placegame/application/__init__.py tests/unit/test_app_bootstrap.py
git commit -m "feat: compose idle application services"
```

The terra report must state:

```text
base and final commits
worktree path and branch
changed files per task
exact fast and integration results
exact Pyright result
whether integration ran or skipped and why
live_contract_unverified as an explicit P2 blocker
all unresolved concerns
```

Then sol performs one read-only review of the final diff. It ends with `Approved` or one finite Critical/Important fix list. The same root cause receives at most two implementation/review cycles.
