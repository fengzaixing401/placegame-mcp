# PlaceGame Reboot and Idle Vertical Slice Design

**Date:** 2026-08-19

**Status:** Approved by recommended-default authorization

**Supersedes for execution order:** the task ordering in the 2026-08-17 MCP core, WebUI, and inventory plans. Those documents remain domain references, not the active implementation sequence.

## 1. Decision

Keep the proven parts of `feat/placegame-automation`, but stop expanding horizontal domain coverage. The product will be delivered as a sequence of complete vertical slices. The first slice is:

```text
account status -> idle preview -> idle collect
```

The slice must be usable from both MCP and a small WebUI before boss, profession, reward, or inventory automation grows further.

The current uncommitted worktree is user-owned work in progress. It must not be reset, overwritten, or treated as a clean release baseline. New implementation starts from an explicitly recorded clean commit or a reviewed preservation commit.

## 2. Product Goal

Build a game automation service that:

- exposes fixed, typed MCP tools so an agent can connect at any time;
- provides a WebUI for direct human control and inspection;
- continues scheduled automation when no agent is connected;
- sends game operations only through typed PlaceGame HTTP API methods;
- applies the same authorization, policy, plan, execution, reconciliation, and audit rules to every caller.

The first usable milestone proves this architecture with account status and idle reward collection.

## 3. Scope

### First milestone

- Preserve and reuse existing account enrollment, secret encryption, account locks, session renewal, typed game client, versioned policy, action-plan lifecycle, reconciliation, and audit storage where they satisfy this design.
- Add a shared application layer for account status, idle preview, and idle execution.
- Expose the shared use cases through Streamable HTTP MCP.
- Expose the same use cases through a loopback-only WebUI and versioned admin API.
- Add persistent idle scheduling only after manual MCP and WebUI flows pass.
- Keep all responses sanitized and correlated with an audit identifier.

### Explicitly deferred

- Boss, profession, safe reward, and inventory expansion.
- Public Internet deployment and public administrator authentication.
- Full dashboard, SSE, TOTP, recovery codes, external notifications, and multi-user tenancy.
- Browser, DOM, Playwright, or image-driven control of the game website.
- Generic URL, HTTP method, headers, or body supplied by an MCP or WebUI caller.

## 4. Architecture

Use a Python 3.12 modular monolith with FastAPI, the Python MCP SDK, SQLAlchemy/PostgreSQL, and a React/TypeScript WebUI.

```text
Agent -- Streamable HTTP MCP --\
                                \
Browser -- Admin JSON API -------+--> Application use cases
                                  |      |-- AccountStatusQuery
Scheduler ------------------------/      |-- IdlePlanUseCase
                                         `-- IdleExecuteUseCase
                                                   |
                                             account lock
                                                   |
                                         authoritative game read
                                                   |
                                          policy + typed plan
                                                   |
                                         typed GameApi mutation
                                                   |
                                         reconcile + verify + audit
```

Transport adapters contain authentication, input validation, error mapping, and response serialization only. They do not implement game rules. The WebUI never calls PlaceGame directly and does not use MCP as its internal API.

## 5. Module Boundaries

### Composition root

`placegame.app` owns application startup and shutdown. It creates the database engine/session factory, game-client factory, repositories, policy and plan stores, account service, application use cases, MCP adapter, and admin routes. It disposes owned resources during shutdown and exposes liveness and readiness separately.

### Application layer

Add focused modules under `placegame.application`:

- `status.py`: list sanitized accounts and return an authoritative status projection for one allowed account.
- `idle.py`: create an idle plan, execute a still-valid plan, and return a stable result.
- `errors.py`: stable application error codes and correlation metadata without transport-specific response classes.

The application layer orchestrates existing account, policy, plan, game, and audit boundaries. It does not accept raw credentials, raw endpoint paths, or untyped dictionaries from transports.

### Existing core

Retain the current crypto, secret framing, account advisory locking, session renewal, strict game schemas, policy versions, plan state machine, timeout reconciliation, and multi-account isolation. Do not extend the 1,260-line `AccountService` with transport or idle-specific behavior. New orchestration belongs in the application layer.

Before implementation, replace the dirty `object + getattr + cast` state-source experiment with typed application ports or concrete typed readers. Production Pyright must report zero errors.

### Transport layer

The MCP adapter exposes only:

- `accounts_list()`
- `account_status(account_id)`
- `idle_preview(account_id)`
- `idle_collect(account_id, plan_id)`
- `automation_status(account_id)` after idle scheduling exists

The initial admin API exposes equivalent versioned routes under `/api/admin/v1`. The first WebUI is a work console, not a marketing or setup site.

## 6. Shared Contracts

Application responses are strict models. Minimum fields are:

```text
AccountSummary: account_id, label, enabled, paused_reason, authenticated
AccountStatus: account, bootstrap identity, idle summary, fetched_at
IdlePreview: optional plan_id, decision, accumulated_seconds, capacity_seconds,
             threshold_seconds, expires_at, reason
IdleExecution: plan_id, status, applied, reconciled, collected summary,
               correlation_id
```

`decision` is `collect` or `wait`. A `wait` preview has no plan ID and cannot be executed. A mutation accepts a server-issued plan ID, not caller-authored action data. Every account selector in the first slice is exactly one account ID; batch selection is deferred.

## 7. Idle Data Flow

### Preview

1. Authenticate the transport caller and authorize the account.
2. Acquire the account service's read/mutation-safe context.
3. Renew the game session if required.
4. Read authoritative bootstrap/status and idle summary.
5. Load the current versioned policy.
6. Decide `collect` or `wait` using the lower of the configured threshold and server capacity.
7. Persist a typed, expiring action plan with policy version and a canonical idle eligibility fingerprint when the decision is `collect`. The fingerprint contains server capacity and whether the threshold is currently satisfied; it does not contain the exact accumulated second, which naturally changes with time.
8. Return a sanitized preview and audit the decision.

### Execute

1. Authenticate and authorize the caller and account.
2. Load the referenced plan and verify ownership, state, expiry, and risk class.
3. Acquire the account mutation lock.
4. Re-read policy and authoritative idle state.
5. Reject stale policy, changed server capacity, or a no-longer-eligible idle state without sending a mutation. Natural accumulation while eligibility remains true does not invalidate the plan.
6. Call only `GameApi.idle_collect()`.
7. Verify that accumulated idle time reset or decreased as expected.
8. On a post-send timeout, reconcile from authoritative state and never blindly retry.
9. Terminalize the plan, persist a sanitized audit result, and return a stable application result.

## 8. Error Model

Stable application error codes include:

- `account_not_found`
- `account_disabled`
- `account_paused`
- `authentication_required`
- `forbidden_account`
- `plan_not_found`
- `plan_not_executable`
- `plan_expired`
- `plan_stale`
- `game_contract_changed`
- `game_temporarily_unavailable`
- `mutation_reconciliation_required`
- `internal_error`

MCP returns structured tool errors; the admin API maps the same errors to appropriate HTTP status codes. Neither surface includes Python traces, credentials, bearer tokens, raw upstream bodies, or verifier exception text.

Schema mismatch stops the affected operation. Ambiguous post-send outcomes are reconciled once and never automatically repeated. The account is paused only when the existing account/session safety policy requires it.

## 9. Security

- MCP tokens are random high-entropy bearer tokens stored as hashes, with scopes and an account allowlist.
- The first WebUI runs on loopback only and uses a development administrator credential or session boundary. It must refuse a non-loopback bind until production authentication is implemented.
- All audit structured fields, including `before`, `after`, `costs`, and `result`, pass through centralized redaction at the repository or persistence boundary.
- Logs and error responses contain correlation IDs but no secrets.
- There is no generic proxy or arbitrary operation tool.

## 10. Testing and Verification

Tests are divided into fast and environment-dependent gates:

- Pure unit tests do not require Docker or PostgreSQL.
- PostgreSQL tests are marked `integration` and skip with a clear reason when no explicit test database or Docker runtime is available.
- A fake game server covers status, idle threshold, collection, session rejection, schema mismatch, conflict, and commit-then-timeout reconciliation.
- Contract fixtures are redacted, versioned, and validated against strict schemas.
- MCP protocol tests cover initialize, tool listing, successful calls, scopes, account allowlists, malformed input, and secret leakage.
- Admin API and WebUI tests cover the same preview and execute behavior.
- Playwright checks the WebUI at desktop and mobile widths for rendering, focus, loading, error, and success states.
- Production Python source must pass Pyright with zero errors.

The standard gate must not report Docker absence as hundreds of test errors. It must either run integration tests or skip them explicitly.

## 11. Delivery Phases

### P0: Baseline and contract gate

- Preserve the dirty worktree and record its relation to commit `97047c3`.
- Establish fast-unit and explicit-integration test markers.
- Restore production Pyright to zero.
- Validate or capture redacted status and idle API fixtures without storing credentials.

### P1: Shared backend idle slice

- Implement the application contracts and use cases.
- Prove fresh-state planning, guarded execution, reconciliation, audit redaction, and two-account isolation.
- Add composition-root lifespan and readiness.

### P2: MCP slice

- Add scoped MCP token authentication and account allowlists.
- Publish the four initial typed tools over Streamable HTTP.
- Pass MCP protocol and leakage tests.

### P3: Loopback WebUI slice

- Build a compact account work console with account selection, status, idle countdown, preview, explicit collect action, and verified result.
- Use the same application use cases through `/api/admin/v1`.
- Pass component, API, desktop, and mobile checks.

### P4: Idle automation

- Add persistent Beijing-time jobs, one scheduler lease holder, idempotency, misfire recovery, pause/resume, run-now, and job history.
- Prove two schedulers cannot duplicate a collection.

### P5: Capability expansion

Add one complete vertical slice at a time in this order: personal/map bosses, world boss, professions, safe rewards, inventory. Each slice requires a verified fixture, typed game client method, shared use case, MCP tool, WebUI control, scheduler hook when applicable, and focused tests. Inventory destructive behavior remains last and plan-confirmed.

### P6: Production publication

Add administrator password, TOTP and recovery codes, CSRF protection, rate limits, Caddy/TLS, backups, restore testing, observability, and production Compose. The P3 loopback credential is removed before public binding is possible.

## 12. Acceptance Criteria for P0-P1

- Existing user WIP is preserved and the implementation baseline is explicit.
- `AccountStatusQuery`, `IdlePlanUseCase`, and `IdleExecuteUseCase` are transport-independent and fully typed.
- A fake game account can return status, produce `wait` or `collect`, and execute one valid collect plan.
- A stale, expired, cross-account, disabled, paused, or non-collect plan sends no game mutation.
- A commit-then-timeout scenario sends exactly one mutation and returns a reconciled or reconciliation-required result.
- Two accounts cannot share credentials, state, plans, or mutations.
- All persisted audit structures are centrally redacted.
- Fast tests pass without Docker; integration tests are explicit; production Pyright reports zero errors.
- No MCP, WebUI, scheduler, boss, profession, reward, or inventory scope is smuggled into the P0-P1 implementation task.

## 13. Agent Handoff

- `gpt-5.6-sol` owns this design, task planning, architectural decisions, and read-only review.
- `gpt-5.6-terra` owns P0-P1 implementation using TDD and the smallest changes that meet the acceptance criteria.
- The implementation handoff must include the diff, commands run, exact results, and unresolved concerns.
- sol review ends with `Approved` or one finite Critical/Important fix list. A maximum of two repair cycles is allowed for the same root cause.
