# PlaceGame P5 Idle Preview Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a durable, read-only idle preview for eligible accounts every five minutes with database-backed single-worker coordination.

**Architecture:** Add one focused scheduler module with a PostgreSQL store for leases, recurring jobs, and idempotent job runs. Inject it into the existing FastAPI lifespan; call only `IdlePlanUseCase.preview` under the existing `AccountService` lock and actor boundary.

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy async PostgreSQL, FastAPI lifespan, pytest/pytest-asyncio, Pyright.

## Global Constraints

- Support only `idle_preview`; never call `idle_collect`.
- Reuse `jobs`, `job_runs`, and `scheduler_leases` from `001_core`; add no migration.
- Fixed `Asia/Shanghai` interval, default 300 seconds; no cron parser or user schedule UI.
- One active database lease named `default`; maximum four concurrent account previews by default.
- Skip and disable work for disabled or paused/removed accounts.
- Keep `live_contract_unverified`; do not add production mutation paths, MCP tools, or WebUI routes.
- Persist and log only sanitized status/error fields, decision, plan id, and correlation id.
- Preserve current resource shutdown ordering and all existing tests.

---

### Task 1: Add scheduler contracts and failing unit tests

**Files:**
- Create: `src/placegame/scheduler.py`
- Modify: `src/placegame/config.py`
- Create: `tests/unit/test_scheduler.py`

**Interfaces:**
- `IdlePreviewScheduler.tick(now: datetime | None = None) -> int`
- `IdlePreviewScheduler.run(stop: asyncio.Event) -> None`
- `IdlePreviewScheduler.close() -> None`
- Constructor dependencies are an async session factory, an account service
  exposing `list_accounts`, an idle preview use case exposing `preview`, a
  clock, worker id, interval seconds, lease seconds, and concurrency limit.

- [ ] **Step 1: Write failing tests**

  Build in-memory fake session/store seams or a SQLite-free fake store around
  the scheduler's explicit store protocol. Cover: lease holder only, one
  deterministic run per account/slot, skipped disabled/paused accounts,
  preview-only delegation with `Actor("scheduler", worker_id)`, per-account
  failure isolation, and semaphore limit four. Assert the fake API has no
  `idle_collect` call.

- [ ] **Step 2: Run the focused tests and confirm red**

  Run:

  ```text
  $env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests/unit/test_scheduler.py -q
  ```

  Expected: collection/import failures because the scheduler module and
  contracts do not exist.

- [ ] **Step 3: Implement the pure scheduler orchestration**

  Add typed protocols/dataclasses for a claimed run and store operations. Use a
  single `asyncio.Semaphore(limit)` around preview calls. Generate a bounded
  correlation id with `uuid4().hex`; map known application/game errors to stable
  codes and map unknown exceptions to `internal_error` without retaining text.
  Advance only the claimed job's next slot. Keep the store injectable so unit
  tests do not need PostgreSQL.

- [ ] **Step 4: Run focused tests green**

  Run the same command and expect all scheduler unit tests to pass.

- [ ] **Step 5: Commit**

  ```text
  git add src/placegame/scheduler.py src/placegame/config.py tests/unit/test_scheduler.py
  git commit -m "feat: add idle preview scheduler core"
  ```

### Task 2: Implement PostgreSQL lease, job provisioning, and idempotent run store

**Files:**
- Modify: `src/placegame/scheduler.py`
- Create: `tests/integration/test_scheduler.py`

**Interfaces:**
- Store methods: `acquire_lease`, `ensure_jobs`, `claim_due`, `finish_run`,
  `release_lease`.
- `Job.kind == "idle_preview"`, `Job.timezone == "Asia/Shanghai"`,
  `Job.schedule == "interval:300"` (using the configured value), and
  `Job.misfire_policy == "defer"`.

- [ ] **Step 1: Add failing PostgreSQL integration tests**

  Upgrade the existing schema to `head`, seed multiple accounts, and assert
  that two workers share one lease; repeated ticks create one run per slot;
  expired leases are reclaimed; stale paused jobs are disabled; and ten
  accounts remain isolated. Keep the integration test marked by the existing
  `postgres_url` fixture.

- [ ] **Step 2: Run integration tests to confirm the missing store behavior**

  ```text
  $env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests/integration/test_scheduler.py -q
  ```

  Expected: failures for missing store methods/behavior. If Docker/PostgreSQL
  is unavailable, record the skip and retain the unit evidence; do not invent
  a local substitute for the transaction checks.

- [ ] **Step 3: Implement transactional PostgreSQL store**

  Use `SELECT ... FOR UPDATE` on the singleton lease and due jobs. Lease
  acquisition succeeds when empty/expired or already owned by this worker and
  sets `lease_expires_at = now + lease_seconds`. Provision only eligible
  accounts; disable existing idle-preview jobs for ineligible accounts. Claim
  due jobs by setting a run lease and inserting a deterministic
  `(account_id, idempotency_key)` row; on `IntegrityError`, reread and skip the
  duplicate. Treat overdue slots as one deferred next slot, not a backlog.
  Finish runs idempotently with redacted JSON and set `completed_at`; never
  store exception strings or secret-bearing objects.

- [ ] **Step 4: Run integration tests green**

  ```text
  $env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests/integration/test_scheduler.py -q
  ```

- [ ] **Step 5: Commit**

  ```text
  git add src/placegame/scheduler.py tests/integration/test_scheduler.py
  git commit -m "feat: persist idle scheduler leases and runs"
  ```

### Task 3: Wire scheduler into application lifespan

**Files:**
- Modify: `src/placegame/app.py`
- Modify: `tests/unit/test_app_bootstrap.py`

**Interfaces:**
- `app.state.idle_preview_scheduler` is the constructed scheduler.
- Lifespan starts one scheduler task after MCP session manager startup and
  cancels/awaits it before closing HTTP and database resources.

- [ ] **Step 1: Add failing lifecycle tests**

  Stub the scheduler with a start/stop observer and assert exactly one start
  and one close on normal lifespan and on MCP startup failure. Preserve the
  existing expected event order for MCP, HTTP, and database cleanup.

- [ ] **Step 2: Run the lifecycle tests and confirm red**

  ```text
  $env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests/unit/test_app_bootstrap.py -q
  ```

- [ ] **Step 3: Wire construction and cancellation**

  Construct the scheduler after `IdlePlanUseCase`, pass existing settings and
  services, create one `asyncio.create_task`, and in `finally` signal/await it
  before `http_client.aclose()` and `database.aclose()`. Do not start it until
  MCP startup succeeds. Handle a missing/closed test stub without changing
  production semantics.

- [ ] **Step 4: Run lifecycle and scheduler tests green**

  ```text
  $env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests/unit/test_app_bootstrap.py tests/unit/test_scheduler.py -q
  ```

- [ ] **Step 5: Commit**

  ```text
  git add src/placegame/app.py tests/unit/test_app_bootstrap.py
  git commit -m "feat: run idle preview scheduler with app lifespan"
  ```

### Task 4: Verification and handoff

**Files:**
- No planned new production files; fix only concrete gate failures.

- [ ] **Step 1: Run focused acceptance tests once**

  ```text
  $env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest tests/unit/test_scheduler.py tests/unit/test_app_bootstrap.py tests/integration/test_scheduler.py -q
  ```

- [ ] **Step 2: Run static checks**

  ```text
  $env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pyright src/placegame/scheduler.py src/placegame/app.py src/placegame/config.py tests/unit/test_scheduler.py
  $env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m compileall -q src tests
  git diff --check
  ```

- [ ] **Step 3: Run the full Python suite once**

  ```text
  $env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest -q
  ```

  Expected: zero failures; integration skips remain environment-dependent.

- [ ] **Step 4: Review scope and secret boundary**

  Search the changed files for `idle_collect`, `password`, `session_token`,
  `Authorization`, and raw exception rendering. `idle_collect` may occur only
  in the negative assertion/test or explicit non-goal documentation; secrets
  must never enter run results, logs, or responses.

- [ ] **Step 5: Handoff to Sol**

  Report commits, test output, integration availability, and the explicit
  deployment note: this phase is safe to ship as a read-only scheduler, but it
  does not enable production idle collection. Sol performs one read-only diff
  review and returns `Approved` or a finite fix list.

