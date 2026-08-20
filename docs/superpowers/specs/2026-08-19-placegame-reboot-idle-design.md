# PlaceGame Single-Operator Automation and Idle Slice Design

**Date:** 2026-08-19

**Status:** Approved after single-operator server scope reduction

**Supersedes for execution order:** the 2026-08-17 core, WebUI, inventory,
and deployment plans. Those documents remain domain references only.

## 1. Product Boundary

PlaceGame MCP is a server-hosted tool for one operator who controls multiple
game accounts. It runs as a Docker container and does not have product users,
tenants, roles, or multiple administrators.

The product will be delivered as complete vertical slices. The first slice is:

```text
account list -> account status -> idle preview -> idle collect
```

The shared backend is implemented first. A remotely accessible Streamable HTTP
MCP adapter and the WebUI consume that backend later, in that order.

The original `placegame-automation` worktree contains user-owned Task 5C WIP.
It remains untouched. P0-P1 work occurs only in the clean sibling worktree
`placegame-idle-v1` on branch `feat/placegame-idle-v1`.

## 2. Goals

- Manage multiple independent PlaceGame accounts for one operator.
- Expose fixed, typed tools to an agent through Streamable HTTP MCP.
- Provide a small WebUI for manual control through the same FastAPI service.
- Run scheduled automation on the server when no agent is connected.
- Build a deployable container image in GitHub Actions without pushing or
  deploying it in P0-P1.
- Send game operations only through fixed, typed PlaceGame HTTP methods.
- Reuse one application layer for MCP, WebUI, and scheduler calls.
- Prevent one account's credentials, state, plans, and mutations from being
  used for another account.
- Prevent duplicate idle collection when local callers race, a request times
  out, or the process exits after the game accepted a mutation.

## 3. Non-Goals

The service does not implement:

- user registration, multiple administrators, RBAC, per-user scopes, 2FA,
  recovery codes, or multi-tenant identity;
- application-managed TLS, domain management, reverse-proxy configuration, or
  automated server deployment;
- generic caller-supplied URLs, methods, headers, or request bodies;
- browser, DOM, Playwright, or image-driven control of the game website;
- boss, profession, reward, inventory, or scheduler behavior in P0-P1.

The later WebUI uses one administrator secret to create a server-side session.
The later MCP endpoint uses one Bearer token. Both values come only from
environment variables or container secrets. P0-P1 does not add RBAC, 2FA, a
user database, or a general token-management subsystem.

## 4. Architecture

Use the existing Python 3.12 modular monolith, FastAPI application core,
SQLAlchemy/PostgreSQL persistence, typed HTTP client, and React/TypeScript
frontend direction.

```text
Agent -- Streamable HTTP MCP ------\
                                    +--> shared application use cases
Browser -- WebUI/admin API --------/       |-- AccountStatusQuery
Scheduler -- server process -------/       |-- IdlePlanUseCase
                                            `-- IdleExecuteUseCase
                                                      |
                                                account isolation
                                                      |
                                           authoritative game state
                                                      |
                                             policy + typed plan
                                                      |
                                            typed GameApi mutation
                                                      |
                                             reconcile + audit
```

Transport adapters validate and serialize requests. They do not contain
game rules. The WebUI never calls PlaceGame directly and does not use MCP as
its internal API.

### Shared backend

`placegame.application` owns transport-independent use cases and strict result
models. It accepts an explicit `account_id` for every account-specific action.

`placegame.app` owns database and HTTP client startup/shutdown, constructs the
repositories and use cases, and exposes liveness/readiness for the server
application. P0-P1 adds no MCP or WebUI product routes.

### MCP

After P1, P2 mounts Streamable HTTP MCP in the FastAPI service. It uses one
Bearer token from an environment variable or container secret and has no RBAC,
scope matrix, or account allowlist. Its initial tools are `accounts_list`,
`account_status`, `idle_preview`, and `idle_collect`. Stdio may remain an
optional development adapter but is not the deployment transport.

### WebUI

After MCP, P3 adds a compact work console and JSON API to the same FastAPI
service. One administrator secret creates a server-side WebUI session. There
is no registration, RBAC, or 2FA. The container listens on `0.0.0.0`; TLS and
the public domain belong to an external reverse proxy.

## 5. Direct Correctness Boundaries

### Multi-account isolation

- Credentials and sessions remain stored per internal account UUID.
- Every state read, plan, execution claim, mutation, and audit record carries
  its account UUID.
- Account advisory locks serialize mutations for the same account while
  allowing different accounts to progress independently.
- A plan created for account A cannot execute against account B.
- Listing never exposes stored credentials or session tokens.

### Sensitive data handling

- Passwords, session tokens, authorization headers, and raw upstream bodies do
  not enter logs, error messages, fixtures, or audit payloads.
- Administrator and MCP secrets exist only in runtime environment variables or
  mounted container secrets. They are not baked into images or CI logs.
- Existing secret encryption remains in use; P0-P1 does not broaden it.
- Correlation IDs and local actor labels are bounded, non-secret identifiers.

### Fixed game boundary

- Only registered typed `GameApi` methods may contact PlaceGame.
- Schema mismatch stops the affected operation.
- Synthetic contract fixtures are marked synthetic and are not represented as
  live evidence.
- Live `idle_collect` exposure remains blocked until an opt-in, credentialed
  response capture is redacted and reviewed.

## 6. Shared Contracts

Minimum application results are:

```text
AccountSummary: account_id, label, enabled, paused_reason, auth_state
AccountStatus: account, bootstrap identity, idle summary, fetched_at
IdlePreview: optional plan_id, decision, accumulated_seconds, capacity_seconds,
             threshold_seconds, expires_at, reason, correlation_id
IdleExecution: plan_id, status, applied, reconciled, collected, correlation_id
```

List `auth_state` is `required` or `unknown`; only an authoritative status read
may return `authenticated`. A `wait` preview has no plan ID. A mutation accepts
only a server-issued plan ID, never caller-authored action data. Batch mutation
is deferred; the first slice executes one account at a time.

## 7. Idle Flow

### Preview

1. Enter the selected account's locked context and renew its game session if
   required.
2. Read authoritative bootstrap and idle summary data.
3. Load the account's versioned policy.
4. Decide `collect` or `wait` using the lower of the configured threshold and
   server capacity.
5. For `collect`, atomically persist one low-risk, no-confirmation
   `IdleCollectAction` plan and its audit record.
6. For `wait`, persist only the audit through the same typed boundary.
7. Return the result with the same correlation ID as the audit.

The eligibility fingerprint contains server capacity and the eligible boolean,
not the exact accumulated second. Natural accumulation after the threshold
therefore does not invalidate the plan; an external collection does.

### Execute

1. Acquire the seed-2 PostgreSQL session execution guard for the account.
2. In a short committed transaction, verify plan ownership, state, expiry,
   policy version, exact single idle action, low risk, and no confirmation.
3. Persist an `executing` claim with owner, attempt count, start time, and a
   two-minute lease before sending the game mutation.
4. Under the normal account lock, re-read policy and authoritative idle state,
   and require the same execution owner and eligibility fingerprint.
5. Call only `GameApi.idle_collect()` and verify that accumulated time reset or
   decreased.
6. Terminalize the claim and audit under the same account mutation
   transaction.

An active guard prevents concurrent execution. An unexpired claim
returns `plan_in_progress`. If the process exits after claim commit, recovery
waits for the lease, acquires the guard, reads authoritative state, and either
marks the plan reconciled or `reconciliation_required`. Recovery never sends
`idle_collect`, even when the state is still eligible.

## 8. Errors

Application errors use stable local codes such as:

- `account_not_found`
- `account_disabled`
- `account_paused`
- `authentication_required`
- `plan_not_executable`
- `plan_in_progress`
- `game_contract_changed`
- `game_temporarily_unavailable`
- `mutation_reconciliation_required`
- `unauthorized`
- `internal_error`

Errors do not include credentials, tokens, raw game responses, or Python
traces. No remote authorization errors are needed.

## 9. Testing

- Fast tests do not require Docker or PostgreSQL.
- PostgreSQL tests are marked `integration` and skip clearly when neither an
  explicit database nor Docker is available.
- Windows development installs `tzdata`; `ZoneInfo("Asia/Shanghai")` must work
  during collection and execution.
- The Docker image has a healthcheck against `/health/live`, runs the service
  on `0.0.0.0`, and contains no repository or runtime secrets.
- GitHub Actions builds the image and performs a container health smoke test;
  it does not push or deploy the image in P0-P1.
- Fake game tests cover status, wait/collect decisions, session rejection,
  schema mismatch, account isolation, timeout reconciliation, racing
  callers, and process exit after mutation commit.
- Contract fixtures include synthetic provenance and pass recursive redaction.
- Production Python source passes Pyright with zero errors.

## 10. Delivery Order

### P0: Development and image baseline

Create a deterministic Windows-friendly baseline: preserve worktree isolation,
add `tzdata`, separate fast and integration tests, restore production Pyright,
record the synthetic/live contract status, and add the Docker/CI build smoke
baseline.

### P1: Shared idle backend

Implement account status, atomic idle preview, crash-safe idle execution,
multi-account isolation, and the application composition root. P1 exposes no
MCP or WebUI product surface.

### Later milestones

1. P2: Streamable HTTP MCP with one Bearer token.
2. P3: WebUI with one administrator secret and server-side session.
3. P4: server-side idle scheduler.
4. P5: one complete game capability slice at a time.

The image listens on `0.0.0.0`. Server rollout, DNS, TLS, and reverse-proxy
configuration remain operator-managed outside P0-P1.

## 11. P0-P1 Acceptance

- The tool is explicitly single-operator and supports multiple isolated game
  accounts.
- The original dirty worktree is unchanged.
- Windows can construct `ZoneInfo("Asia/Shanghai")`, and test collection does
  not fail because timezone data is missing.
- GitHub Actions can build the Docker image and verify its healthcheck without
  pushing or deploying it.
- A fake account can return status, preview wait/collect, and execute one valid
  idle plan.
- Stale, expired, cross-account, disabled, paused, or malformed plans send no
  mutation.
- Timeout, racing calls, and process-exit recovery never send a duplicate idle
  mutation.
- Credentials and session tokens are absent from logs, errors, fixtures, and
  audit payloads.
- Fast tests run without Docker; integration tests run or skip explicitly;
  production Pyright is clean.
- P0-P1 contains no MCP adapter, WebUI, scheduler, RBAC, 2FA, or deployment
  automation beyond building and smoke-testing the image.

## 12. Agent Handoff

- `gpt-5.6-sol` owns planning and read-only milestone review.
- `gpt-5.6-terra` or Luna implements P0 and P1.
- Terra runs one relevant verification gate at the end of each milestone.
- Sol reviews only after P0 and P1 completion, not after internal task steps.
- Each milestone allows at most one focused fix and one focused re-review.
- A handoff includes the diff/commit, exact commands and results, skipped
  integration reason if any, and unresolved contract evidence.
