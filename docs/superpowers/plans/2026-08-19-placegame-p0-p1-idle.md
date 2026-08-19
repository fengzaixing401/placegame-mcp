# PlaceGame Server P0-P1 Implementation Plan

**Goal:** Establish a Windows-friendly development and Docker build baseline,
then deliver the shared idle backend for one operator with multiple game
accounts.

**Implementation owner:** `gpt-5.6-terra` or Luna.

**Review owner:** `gpt-5.6-sol` after each milestone only.

## Global Constraints

- Work only in `D:\Ai\placegame-mcp\.worktrees\placegame-idle-v1` on branch
  `feat/placegame-idle-v1`.
- Do not modify, reset, clean, or commit the user-owned dirty Task 5C worktree
  at `D:\Ai\placegame-mcp\.worktrees\placegame-automation`.
- The product is server-hosted, single-operator, and multi-game-account.
- P0-P1 adds no MCP adapter, WebUI, scheduler, RBAC, 2FA, or multi-user system.
- Later P2 adds Streamable HTTP MCP with one Bearer token. Later P3 adds a
  WebUI using one administrator secret and server-side session. Both use the
  shared FastAPI backend implemented here.
- The application container listens on `0.0.0.0`. TLS, domain, and reverse
  proxy configuration are external concerns.
- Preserve per-account credentials, locks, plans, and mutation isolation.
- Never write credentials, session tokens, authorization headers, or raw game
  responses to logs, errors, fixtures, or audit records.
- Never retry an ambiguous idle mutation by sending it again.
- Terra runs the milestone gate once at the end of P0 and once at the end of
  P1. Sol reviews only those two completed milestone diffs.
- A failed milestone review gets one focused fix and one focused re-review.

## Milestone P0: Development and Image Baseline

### Scope

P0 makes the existing clean branch deterministic on Windows, separates fast
feedback from environment-dependent tests, and proves the deployable image can
be built and become healthy. It does not add application features.

### Files

Modify:

- `pyproject.toml`
- `uv.lock`
- `tests/conftest.py`
- `src/placegame/config.py`
- `Dockerfile`
- `.dockerignore`
- `.github/workflows/build-image.yml`

Create:

- `tests/fixtures/game/v1/bootstrap.json`
- `tests/fixtures/game/v1/idle-summary.json`
- `tests/fixtures/game/v1/idle-collect.json`
- `tests/contract/test_idle_contract_fixtures.py`
- `docs/contracts/placegame-idle-contract-status.md`

### Required Work

1. Record `git status --short --branch`, `git rev-parse HEAD`, and the original
   dirty worktree status. Stop if the implementation worktree is not clean or
   the original WIP no longer matches the recorded nine dirty Task 5C files.
2. Add the maintained `tzdata` package as a runtime dependency and update the
   lock file. Do not add custom timezone fallback code.
3. Add a focused regression proving both
   `ZoneInfo("Asia/Shanghai")` construction and pytest collection succeed on
   Windows without relying on system IANA timezone files.
4. Register the `integration` pytest marker. Mark every test whose fixture
   graph includes `postgres_url`. Keep `PLACEGAME_TEST_DATABASE_URL` as the
   preferred database path; if it is absent and Docker is unavailable, skip
   integration tests with one clear reason instead of emitting setup errors.
5. Fix `Settings.from_env()` through typed Pydantic validation so production
   Pyright has no constructor-signature error. Do not suppress type checking.
6. Add three synthetic minimum contract fixtures for bootstrap, idle summary,
   and idle collection. Each fixture records:

```text
provenance=synthetic
endpoint
created_at
verified_at=null
game_contract_version=unverified
redaction_method=placegame.security.redaction.redact
live_contract_status=unverified
```

7. Contract tests validate the typed schemas and require
   `redact(document) == document`.
8. Record `live_contract_unverified` in
   `docs/contracts/placegame-idle-contract-status.md`. A later P2 may test MCP
   against the fake server but cannot expose live `idle_collect` until an
   opt-in credentialed capture is redacted and reviewed.
9. Add a Python-only production `Dockerfile` that starts Uvicorn with
   `placegame.app:create_app --factory`, binds `0.0.0.0:8000`, and defines a
   healthcheck against `http://127.0.0.1:8000/health/live`. Do not bake any
   administrator secret, MCP token, game credential, database password, or
   master key into the image.
10. Add `.dockerignore` for `.git`, worktrees, virtual environments, caches,
    test output, local environment files, and secrets.
11. Add `.github/workflows/build-image.yml` for pull requests and pushes. It
    builds the image, starts it with generated CI-only test secrets, waits for
    the healthcheck, and removes the container. It does not log in to a
    registry, push an image, or deploy to a server.

### P0 Acceptance

- The implementation worktree remains isolated and the original dirty
  worktree is unchanged.
- `ZoneInfo("Asia/Shanghai")` succeeds on Windows.
- Fast test collection and execution do not require Docker.
- Integration tests are selected explicitly and either run or skip clearly.
- Synthetic fixtures are provenance-marked, schema-valid, and redacted.
- Production Pyright reports zero errors.
- Docker builds successfully, starts on `0.0.0.0`, and reaches healthy state.
- CI contains build and health smoke only; it has no push or deploy step.
- No product behavior or transport surface is added.

### P0 Gate

Terra runs this gate once after all P0 changes:

```powershell
.\.venv\Scripts\python.exe -c "from zoneinfo import ZoneInfo; print(ZoneInfo('Asia/Shanghai'))"
.\.venv\Scripts\python.exe -m pytest -m "not integration" -q
.\.venv\Scripts\python.exe -m pytest --collect-only -m integration -q
.\.venv\Scripts\python.exe -m pyright src/placegame
docker build -t placegame-mcp:p0 .
# Start with generated CI-only environment values, wait for healthy, then stop.
git diff --check
git status --short --branch
```

Terra then creates one P0 milestone commit and reports the exact results. Sol
performs one read-only P0 review. If required, Terra makes one focused fix and
Sol performs one focused re-review.

## Milestone P1: Shared Multi-Account Idle Backend

### Scope

P1 implements the shared account status and idle application use cases. It
retains the existing typed game client, encrypted account storage, session
renewal, policy versions, account locks, and plan state machine. It does not add
MCP, WebUI, or scheduler code.

### Files

Create:

- `src/placegame/application/__init__.py`
- `src/placegame/application/errors.py`
- `src/placegame/application/models.py`
- `src/placegame/application/status.py`
- `src/placegame/application/idle.py`
- `migrations/versions/003_action_plan_execution_claim.py`
- `tests/unit/test_application_status.py`
- `tests/unit/test_application_idle.py`
- `tests/integration/test_idle_application.py`

Modify only as required:

- `src/placegame/contracts.py`
- `src/placegame/security/redaction.py`
- `src/placegame/models.py`
- `src/placegame/accounts/repository.py`
- `src/placegame/accounts/locks.py`
- `src/placegame/accounts/service.py`
- `src/placegame/policy/plans.py`
- `src/placegame/policy/engine.py` for idle delegation only
- `src/placegame/db.py`
- `src/placegame/app.py`
- focused existing tests affected by these contracts

Do not port any dirty Task 5C boss, profession, reward, or state-source code.

### Application Contracts

Use strict, frozen result models:

```python
class AccountSummary:
    account_id: UUID
    label: str
    enabled: bool
    paused_reason: str | None
    auth_state: Literal["authenticated", "required", "unknown"]

class AccountStatus:
    account: AccountSummary
    bootstrap_account_id: str
    idle: IdleState
    fetched_at: datetime

class IdlePreview:
    account_id: UUID
    plan_id: UUID | None
    decision: Literal["collect", "wait"]
    accumulated_seconds: int
    capacity_seconds: int
    threshold_seconds: int
    expires_at: datetime | None
    reason: str
    correlation_id: str

class IdleExecution:
    account_id: UUID
    plan_id: UUID
    status: Literal["executed", "reconciled"]
    applied: bool
    reconciled: bool
    collected: bool
    correlation_id: str
```

Account listing sorts by label then UUID. It returns only `required` or
`unknown`; only an authoritative snapshot can return `authenticated`.

### Shared Status Use Case

1. Add deterministic account listing to the repository and service.
2. Preserve the real local actor in locked account contexts for audit
   correlation; do not replace it with a scheduler placeholder.
3. `AccountStatusQuery` reads an authoritative account snapshot and converts
   it through a private strict schema. Validation failure becomes a safe
   `game_contract_changed` error without embedding invalid state.
4. Status results contain no credentials or session token fields.

### Atomic Idle Preview

1. `IdlePlanner.threshold` returns the lower of configured threshold and server
   capacity.
2. Its canonical fingerprint contains `capacitySeconds` and `eligible`, not
   exact accumulated seconds. Policy version remains a separate plan guard.
3. `collect` creates exactly one `SelectedDecision(IdleCollectAction)`,
   `risk="low"`, `confirmation_required=False`, zero estimated costs, and a
   five-minute expiry. `wait` creates no plan.
4. A typed `IdlePreviewStore` owns one transaction containing the optional plan
   and mandatory `idle.preview` audit record. Audit failure rolls back plan
   creation.
5. Actor labels and correlation IDs are bounded ASCII identifiers. All audit
   JSON fields pass through the existing recursive redaction boundary.

### Crash-Safe Idle Execution

Add durable plan fields through migration `003_action_plan_execution_claim`:

```text
execution_owner
execution_started_at
execution_lease_expires_at
execution_attempt_count default 0
```

Execution follows this closed protocol:

1. Acquire a PostgreSQL session advisory guard using hash seed 2. It uses a
   connection separate from the normal seed-0 account transaction lock and is
   held across claim commit, game I/O, and terminal commit.
2. In a short transaction, row-lock the plan by `plan_id` and `account_id`.
   Accept only one selected `IdleCollectAction`, low risk, no confirmation,
   valid expiry, and a non-terminal state.
3. `pending`/`confirmed` becomes `executing` with a random owner, attempt count
   increment, start time, and two-minute lease. The claim and its audit commit
   before game I/O. Audit failure rolls the claim back.
4. An unexpired executing claim returns `plan_in_progress` and sends nothing.
5. An expired executing claim may transfer recovery ownership only while the
   seed-2 guard is held. Recovery does not increment attempt count and can
   never return execute mode.
6. Execute mode acquires the normal account lock, verifies execution owner,
   current policy version, and the idle eligibility fingerprint, then calls
   only `GameApi.idle_collect()`.
7. A normal result or timeout is verified from authoritative idle state. The
   terminal plan update and audit require the same execution owner.
8. Process-exit recovery reads current idle state only. If collection can be
   proven it marks executed/reconciled; otherwise it marks
   `reconciliation_required`. It never calls `idle_collect`.

Keep the legacy generic mutation path for committed deferred code. The new idle
use case must call only the claimed path.

### Multi-Account and Sensitive-Data Tests

P1 tests prove:

- account A's plan cannot execute for account B;
- same-account mutations serialize while different accounts remain isolated;
- duplicate local callers produce one send and one `plan_in_progress` result;
- stale, expired, disabled, paused, high-risk, confirmation-required, extra
  decision, and cross-account plans send zero mutations;
- commit-then-timeout sends once and reconciles or reports reconciliation
  required;
- process exit after game commit leaves the durable claim; after lease expiry,
  recovery sends nothing and mutation count remains one;
- process exit before send recovers without sending;
- credentials, tokens, authorization headers, cookies, and raw responses are
  absent from returned models, errors, fixtures, and persisted audit payloads.

### Composition Root

Replace session-only database construction with an owned `Database` containing
the async engine and session factory. `placegame.app` creates one shared HTTP
client, repositories, policy service, account service, preview store, execution
guard, claims, and the three application use cases. It closes HTTP and database
resources exactly once.

P1 exposes only `/health/live` and database-backed `/health/ready`. Uvicorn in
the container binds `0.0.0.0:8000`. P1 adds no MCP, admin, or WebUI routes.
Readiness never returns database exception text.

### P1 Acceptance

- `AccountStatusQuery`, `IdlePlanUseCase`, and `IdleExecuteUseCase` are strict,
  typed, and transport-independent.
- At least two fake game accounts remain isolated across credentials, status,
  plans, locks, mutations, and audits.
- Wait and collect previews behave deterministically; preview plan and audit
  are atomic.
- Valid idle collection sends once and returns a verified result.
- All negative plan cases send zero mutations.
- Timeout, concurrent callers, and process exit cannot cause a duplicate idle
  send.
- Owned database and HTTP resources close exactly once; readiness is safe.
- No P2/P3 transport, administrator session, or Bearer-token behavior is added.

### P1 Gate

Terra runs this gate once after all P1 changes:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration" -q
.\.venv\Scripts\python.exe -m pytest -m integration -q
.\.venv\Scripts\python.exe -m pyright src/placegame
git diff --check
git status --short --branch
```

Integration tests run with `PLACEGAME_TEST_DATABASE_URL` or Docker; otherwise
they skip explicitly and the handoff names the skipped coverage.

Terra then creates one P1 milestone commit and reports the exact results. Sol
performs one read-only P1 review. If required, Terra makes one focused fix and
Sol performs one focused re-review.

## Handoff Contract

Each P0/P1 handoff contains:

```text
milestone and commit
changed files
exact gate commands and results
integration environment or skip reason
confirmation that the original dirty worktree is unchanged
live contract status
unresolved correctness concerns
```

Implementation stops after P1. P2 Streamable HTTP MCP and P3 WebUI/session
behavior require their own later plans.
