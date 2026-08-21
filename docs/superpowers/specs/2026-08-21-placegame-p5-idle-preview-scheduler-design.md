# PlaceGame P5 Idle Preview Scheduler Design

## Goal

Add the first server-side automation loop as a read-only idle-preview scheduler.
For enabled, unpaused game accounts, the service periodically calls the existing
`IdlePlanUseCase.preview` and records a durable run. It never calls
`idle_collect`, changes game state, or exposes a new MCP/WebUI operation.

## Context And Boundary

P0-P4 already provide encrypted multi-account persistence, account locking,
typed idle preview planning, WebUI account administration, and the `jobs`,
`job_runs`, and `scheduler_leases` tables from `001_core`. The live game
contract is explicitly `live_contract_unverified`; therefore production idle
execution and manual collection remain out of scope. This slice proves the
server loop and durable coordination without claiming that a mutation is safe.

## Decisions

- One job kind is supported: `idle_preview`.
- Each enabled account has at most one recurring idle-preview `Job`.
- The schedule is a fixed interval in `Asia/Shanghai`, defaulting to five
  minutes. The interval is configuration, not a user-supplied cron expression.
- A PostgreSQL row lease named `default` elects one scheduler instance per tick.
  Lease acquisition and due-job claiming happen in short transactions.
- A due run has an idempotency key derived from account, job kind, and scheduled
  slot. A unique database constraint prevents duplicate dispatch after retries or
  concurrent ticks.
- At most `max_account_concurrency` previews run at once; the existing default
  is four. Different accounts may run concurrently; account locks serialize work
  for one account.
- Disabled accounts and accounts paused for any reason, including `removed`, are
  not provisioned or dispatched. Existing jobs are disabled when observed.
- A misfired interval is deferred to the next slot rather than replaying an
  unbounded backlog. The run result is a sanitized status/code summary and may
  include the preview decision and plan identifier, never credentials or raw
  exceptions.
- Scheduler errors are isolated per account. A failed preview completes its
  `JobRun` with a stable error code and does not stop other accounts.
- Lifespan starts one background loop after MCP startup and cancels it before
  closing the HTTP client/database. Cancellation is awaited and is idempotent.

## Components And Interfaces

`src/placegame/scheduler.py` owns the bounded implementation:

```python
class IdlePreviewScheduler:
    async def tick(self, now: datetime | None = None) -> int: ...
    async def run(self, stop: asyncio.Event) -> None: ...
    async def close(self) -> None: ...
```

The scheduler accepts an async session factory, `AccountService`,
`IdlePlanUseCase`, a clock, worker id, interval, lease duration, and concurrency
limit. A small internal store performs lease acquisition, account-job
provisioning, due-run claims, and terminal run updates. The store uses
`SELECT ... FOR UPDATE` for lease/job rows and catches unique conflicts by
re-reading the existing run.

`Settings` adds only `scheduler_interval_seconds` (default `300`) and
`scheduler_worker_id` (default a generated process-local identifier when not
provided). Existing `scheduler_lease_seconds` and
`max_account_concurrency` remain the lease and semaphore controls.

`create_app` constructs the scheduler from the already-created account service
and idle preview use case. The lifespan owns its task. No route, MCP registry,
database migration, or game client allowlist changes are needed.

## Data Flow

1. `tick` obtains the `default` scheduler lease. If another worker owns an
   unexpired lease, it returns zero without touching jobs.
2. In one short transaction it lists current accounts, ensures one
   `idle_preview` job for each eligible account, disables stale jobs for
   disabled/paused accounts, advances overdue schedules to the next interval,
   and claims due jobs with a run lease and deterministic idempotency key.
3. Claimed runs execute under a semaphore. Each calls
   `IdlePlanUseCase.preview(account_id, actor=Actor("scheduler", worker_id),
   correlation_id=...)` exactly once.
4. Success stores a redacted result containing `status`, `decision`, `plan_id`,
   and `correlation_id`; failure stores `status="failed"` and a stable error
   code using the existing application/game error mapping. The job's next slot
   is advanced transactionally.
5. The next tick observes the durable state, so process restarts and expired run
   leases do not create duplicate previews.

## Failure And Shutdown Rules

- Lease loss prevents claiming new work; already claimed work may finish and is
  recorded with its existing run lease.
- A cancelled task does not call `idle_collect` or retry a preview. The run is
  left recoverable after its lease expires.
- Database failures are logged with worker/job/account identifiers only and are
  re-raised from the store; the loop backs off for one interval and remains
  stoppable.
- No secret, authorization header, session token, password, or raw exception
  text appears in `JobRun.result`, audit JSON, logs, or test output.

## Acceptance Criteria

- Two scheduler instances dispatch only while one holds the database lease.
- Repeated ticks produce one run per account/slot, including after a unique-key
  race or process restart.
- Expired scheduler/run leases can be reclaimed.
- Enabled accounts are isolated and no more than four previews are concurrent.
- Disabled, paused, and removed accounts produce no new preview call.
- A preview failure is isolated and represented by a stable sanitized result.
- The scheduler invokes `IdlePlanUseCase.preview` and never `idle_collect`.
- Lifespan starts and stops the scheduler exactly once and still closes MCP,
  HTTP, and database resources on startup failure.
- Existing P0-P4 tests remain green; no new migration is generated.

## Explicit Non-Goals

No idle collection, live credential capture, boss/profession/reward handling,
inventory cleanup, cron editor, scheduler WebUI, MCP tool, token/RBAC work, or
new persistence tables.

