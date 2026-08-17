# PlaceGame Multi-Account MCP Core Design

**Date:** 2026-08-17  
**Status:** Approved design, pending written-spec review  
**Related specifications:**

- `2026-08-17-placegame-inventory-design.md`
- `2026-08-17-placegame-webui-design.md`

## 1. Goal

Build a remotely hosted, multi-account management service for PlaceGame. The service exposes safe MCP tools to AI agents, runs reliable Beijing-time automation while agents are offline, and performs every game operation through the game's HTTP API. It must never automate the game through browser clicks, DOM access, Playwright, or image recognition.

## 2. Scope

The core delivers:

- Multiple independent game accounts with per-account credentials, policy, state, schedules, locks, and audit history.
- Direct HTTP integration with `https://game.placegame.cn/api/*`.
- A Streamable HTTP MCP endpoint for remote agents.
- Persistent scheduling for idle rewards, personal bosses, map/solo bosses, world-boss collaboration, professions, safe reward claims, and inventory maintenance.
- A policy engine shared by MCP commands, WebUI actions, and scheduled jobs.
- Plan-before-execute handling for resource-spending and destructive operations.
- PostgreSQL-backed job state, idempotency, leases, and audit records.

The first release explicitly excludes:

- Browser-driven game automation.
- Arbitrary pass-through HTTP tools.
- Automated market buying, selling, or listing.
- Automated equipment enhancement, reforging, quality upgrading, unbinding, or inheritance.
- Automatic selection or change of a permanent profession specialization.
- Multi-tenant end-user accounts. The product has one administrator and multiple game accounts.

## 3. Architectural Decision

Use a modular monolith for application logic, deployed with PostgreSQL and Caddy through Docker Compose.

```text
Remote Agent ── MCP/HTTPS ─┐
                           ├── Caddy ── Application
Administrator ─ Web/HTTPS ┘               │
                                           ├── MCP adapter
                                           ├── Admin API + WebUI
                                           ├── policy engine
                                           ├── scheduler
                                           ├── account workers
                                           └── game HTTP client
                                                     │
                                                     ▼
                                            game.placegame.cn

Application ── PostgreSQL: configuration, jobs, locks, plans, audit
```

This keeps all callers on one policy path. PostgreSQL provides durable coordination without adding Redis or a message broker. The modules use explicit interfaces so they can be separated later if account volume requires it.

## 4. Deployment Topology

Docker Compose contains:

- `app`: Python 3.12 asynchronous application, MCP endpoint, admin API, scheduler, and built WebUI assets.
- `postgres`: PostgreSQL 16 with a persistent volume and health checks.
- `caddy`: TLS termination, HTTPS redirects, request limits, and security headers.

The application is deployed as one active scheduler instance. HTTP request handling may use multiple async tasks, but only the elected scheduler lease holder dispatches timed jobs. Backups include the PostgreSQL database, Caddy state, and the encrypted-secret master key. The master key is stored separately from database backups.

## 5. Core Module Boundaries

### 5.1 Game API client

Responsibilities:

- Add `Authorization: Bearer <session-token>` without exposing the token to callers or logs.
- Apply timeouts, response-envelope parsing, redaction, bounded retries, and per-account rate limits.
- Support the observed read endpoints:
  - `GET /api/client/bootstrap`
  - `GET /api/client/catalog`
  - `GET /api/client/idle-summary`
  - `POST /api/client/view-sections`
- Support only explicitly registered mutation endpoints, including:
  - `POST /api/auth/login`
  - `POST /api/battle/idle-collect`
  - `POST /api/boss/preview`
  - `POST /api/boss/challenge`
  - `POST /api/boss/assist`
  - `POST /api/professions/settle`
  - `POST /api/professions/queue/enqueue`
  - `POST /api/professions/supply/equip`
  - safe reward and inventory endpoints described in the related specifications.

The client does not accept a URL or path supplied by an MCP caller. Each operation maps to a typed internal method.

### 5.2 Account service

Responsibilities:

- Create, validate, enable, disable, pause, resume, and remove managed accounts.
- Resolve credentials and renew sessions.
- Maintain a short-lived state cache while treating the game server as authoritative.
- Acquire a PostgreSQL advisory lock for every account mutation.
- Expose a sanitized account snapshot to MCP and WebUI callers.

### 5.3 Policy engine

Responsibilities:

- Convert server state and per-account policy into an action plan.
- Classify actions as read-only, low-risk automatic, confirmation-required, or forbidden.
- Produce structured reasons for every selected, skipped, or blocked action.
- Execute only a still-valid plan after checking current server state and policy version.

### 5.4 Scheduler

Responsibilities:

- Store schedule definitions and next-run state in PostgreSQL.
- Dispatch jobs in `Asia/Shanghai`, independent of VPS local timezone.
- Recover misfires after restart according to each job's policy.
- Limit normal account concurrency to four by default.
- Prioritize world-boss windows over ordinary maintenance jobs.

### 5.5 MCP adapter

Responsibilities:

- Authenticate the caller's MCP token.
- Enforce token scopes and allowed account IDs.
- Validate typed tool inputs and output only sanitized structured results.
- Delegate all decisions and mutations to the policy engine.

### 5.6 Audit service

Responsibilities:

- Record actor, source, account, plan, action, resource cost, result, and correlation ID.
- Redact credentials, authorization headers, TOTP secrets, and full MCP tokens.
- Retain operational audit records for 90 days by default.

## 6. Persistent Data Model

### `game_accounts`

- `id`: internal UUID used by tools and URLs.
- `label`: administrator-visible name.
- `game_username`: encrypted or null for token-only accounts.
- `auth_mode`: `credentials` or `token_only`.
- `encrypted_password`: nullable encrypted value.
- `encrypted_session_token`: encrypted value.
- `session_expires_at`: game token expiry.
- `enabled`: whether all automation is allowed.
- `paused_reason`: nullable manual or automatic pause reason.
- `policy_version`: monotonically increasing integer.
- `created_at`, `updated_at`, `last_success_at`, `last_error_at`.

### `account_policies`

One validated JSON document per account. It contains idle thresholds, boss thresholds, resource reserves, profession stock targets, inventory rules, enabled schedules, and notification preferences. Unknown fields are rejected.

### `account_snapshots`

Sanitized snapshots used for dashboards and diagnostics. Credentials and raw authorization data are never stored here. Snapshots expire logically after five minutes and are never used as the sole precondition for a mutation.

### `jobs` and `job_runs`

`jobs` contains logical recurring jobs. `job_runs` contains dispatch time, idempotency key, lease owner, attempt count, result, and next retry. A unique constraint on `(account_id, idempotency_key)` prevents duplicate logical actions.

### `action_plans`

Contains the state fingerprint, policy version, proposed actions, estimated costs, risk classification, expiry, and execution state. Confirmation-required plans expire after five minutes.

### `mcp_tokens`

Stores token prefix, SHA-256 token digest, scopes, allowed account IDs, expiry, last use, and revocation state. The full high-entropy token is shown only once at creation.

### `audit_events`

Append-only records with structured before/after summaries. Raw game response bodies are not retained unless an administrator temporarily enables redacted diagnostic capture.

## 7. Credential Security and Session Renewal

- A 256-bit master key is mounted as a Docker secret.
- Game usernames, passwords, session tokens, and TOTP secrets are encrypted with AES-256-GCM using a fresh nonce per value and record-bound authenticated data.
- Credential-mode accounts automatically log in when a token is absent, rejected, or within 24 hours of expiry.
- Token-only accounts pause with `session_refresh_required` when renewal is necessary.
- Failed login is retried twice with increasing delay. Three consecutive authentication cycles failing within one hour pause only that account and create a critical alert.
- No API response exposes decrypted credentials.

## 8. Account Concurrency and State Reconciliation

Every mutation follows this sequence:

1. Acquire the account advisory lock.
2. Refresh the minimum authoritative state needed for the action.
3. Re-evaluate policy and plan preconditions.
4. Send one typed mutation request.
5. Refresh state and verify the expected state transition.
6. Commit the result and audit event, then release the lock.

If the server reports that another worker updated the player, the action refreshes state and retries at most twice with jitter. If a network timeout occurs after sending a mutation, the service never blindly repeats it; it first checks counters, instance IDs, inventory, or timestamps to determine whether the mutation applied.

## 9. Default Automation Policy

All defaults are overrideable per account except permanent-specialization protection and forbidden operation classes.

### 9.1 Idle rewards

- Poll idle summary every five minutes.
- Collect at 11 hours 30 minutes of valid accumulation.
- At 11 hours 50 minutes, enter emergency mode and retry at 30, 60, and 120 seconds.
- Never allow the service's configured threshold to exceed the server-provided capacity, currently 12 hours.
- Run inventory capacity planning before collection when bag usage is at least 85%.

### 9.2 Personal bosses

- Run after Beijing daily reset and when the shared personal pool reports free attempts.
- Use free attempts only by default; paid extra attempts are disabled.
- Evaluate eligible bosses from highest required level downward.
- For each boss, evaluate difficulty in `nightmare`, `hard`, `normal` order.
- Challenge only when the final preview has `predictedWin=true` and `chance >= 80`.
- Failed challenges return the opportunity according to current game text, but the service still re-reads the pool before retrying.

### 9.3 Map and ordinary solo bosses

- Poll the boss section every minute.
- Use server `attempts`, `blockedReason`, difficulty availability, and refresh keys instead of hard-coded refresh times.
- Mirror the site's ordinary solo-boss category: entries with type `map`, plus type `world` entries that expose ordinary solo attempts.
- Never attempt a boss whose best normal configuration fails the minimum preview threshold.

### 9.4 World-boss collaboration

- Use `POST /api/boss/assist`, not ordinary challenge configuration.
- Schedule Beijing windows 10:00-11:00, 16:00-17:00, and 20:00-21:00.
- Wake 15 seconds before each opening and poll current world instances once per second until active or until five minutes after opening.
- For every unlocked, active, still-alive world boss, submit collaboration until `remainingAttemptCount` is zero, normally three attempts.
- Each attempt is verified by `myAttemptCount` and `remainingAttemptCount` before another is sent.
- Skip defeated or ended instances and record the reason.
- World-boss work has scheduler priority and may use all configured account concurrency slots.

### 9.5 Boss loadout optimizer

For each difficulty:

1. Generate the same three bounded skill candidates as the game UI: output, survival, and balanced, with at most three skills each.
2. Preview those candidates against `none`, `assault`, `guard`, and `focus` with no reward affix, for at most 12 baseline previews.
3. Keep the best three configurations by predicted result, chance, remaining player HP, and boss HP.
4. Test available affixes in descending reward multiplier against those configurations, for at most 12 additional previews.
5. Select the highest multiplier that still meets `predictedWin=true` and the account's minimum chance, default 80.
6. Cache the winning configuration by boss, difficulty, equipment fingerprint, skill fingerprint, active potion, and combat-balance version. Revalidate a cached configuration with one preview before use.

`useMaterialBoost` is considered only after combat configuration is selected because it changes rewards rather than combat. It is enabled only on hard or nightmare, when owned material after cost remains at or above the default reserve of 64. The target slot is the lowest-score currently equipped eligible slot.

Boss potions are not consumed on easy fights by default. If no potion-free configuration meets the threshold, the planner selects an owned potion matching shield, output, or survival bottleneck, respects the configured potion reserve, equips it, and performs a final preview before challenging.

### 9.6 Professions

- Read and preserve `selectedProfessionKey`; the service never calls `/api/professions/select`.
- Settle progress every five minutes when a queue exists.
- Refill when fewer than two queue entries remain or predicted work duration falls below six hours.
- Keep no more than five queue entries and plan at least 12 hours of executable work when materials allow.
- Prioritize reaching action unlock milestones, then maintain configured food and boss-potion stocks, then gather required inputs.
- Default stock targets are six units of the selected high-tier food and twelve units of each selected boss potion. Accounts may override these targets.
- Supply changes are serialized with boss and idle operations under the account lock.

### 9.7 Safe reward claims

Completed quests, daily activity rewards, achievements, codex rewards, and mail rewards with no choice or cost may be claimed automatically. Choice rewards, inventory-overflowing claims, and any claim with a cost require confirmation.

## 10. MCP Surface

### Read scope (`game:read`)

- `accounts_list`
- `account_status`
- `idle_preview`
- `bosses_list`
- `boss_optimize`
- `professions_status`
- `inventory_list`
- `jobs_list`
- `audit_logs`

### Operate scope (`game:operate`)

- `idle_collect`
- `boss_run_cycle`
- `world_boss_participate`
- `professions_maintain`
- `inventory_cleanup_plan`
- `inventory_cleanup_execute`
- `warehouse_transfer`
- `rewards_claim_safe`

### Automation scope (`automation:manage`)

- `automation_status`
- `automation_pause`
- `automation_resume`
- `automation_run_now`
- `policy_get`
- `policy_update`

### Administration scope (`admin`)

- Account credential management remains WebUI-only in the first release.
- MCP token creation, rotation, and revocation remain WebUI-only.

Every account-targeting tool accepts exactly one of `account_id`, `account_ids`, or `all_enabled`. Tokens may further restrict usable account IDs. Batch mutation results are returned per account and partial failure never rolls back successful work on another account.

## 11. Error Handling

- Read timeout: retry up to three times with jitter.
- Mutation timeout: reconcile state before any retry.
- HTTP 401/403: renew credential-mode sessions or pause token-only accounts.
- HTTP 426: pause all game mutations and alert that the client contract changed.
- Schema mismatch: stop the affected operation, retain raw redacted metadata, and alert.
- Inventory full: invoke the inventory planner; never silently broaden decomposition rules.
- Insufficient resource: skip with exact owned, cost, and reserve figures.
- Repeated server conflict: defer the job for one minute after two reconciled attempts.

## 12. Observability

- Structured JSON application logs with correlation and account IDs, never credentials.
- Health endpoints for process, database, scheduler lease, and game connectivity.
- Dashboard counters for job success, retries, missed windows, API latency, authentication failures, and inventory pressure.
- Alerts in WebUI for paused accounts, token expiry, world-boss misses, repeated failures, and unsafe inventory pressure.

External notification channels are not required in the first release, but audit and alert interfaces must allow later email, Telegram, or webhook adapters.

## 13. Testing Strategy

- Unit tests for policy selection, time windows, resource reserves, permissions, redaction, and plan expiry.
- Contract tests against recorded and fully redacted game response fixtures.
- An in-process fake game server for login expiry, conflicts, timeouts, counters, boss instances, and inventory mutations.
- Scheduler tests with a controllable clock fixed to `Asia/Shanghai`.
- Multi-account isolation tests that inject failures into one account while others proceed.
- Idempotency tests for timeout-after-commit scenarios.
- MCP protocol tests for scope, account restrictions, schema validation, and secret redaction.
- No automated test uses a real game account unless an administrator explicitly runs a separate, read-only integration profile.

## 14. Acceptance Criteria

- At least ten configured accounts can run independently without state or credential crossover.
- An offline Agent does not prevent scheduled tasks from running.
- Every game mutation is attributable to a scheduler, WebUI administrator, or MCP token.
- No browser is installed or required in the production application image.
- World-boss jobs use the collaboration endpoint and verify all three attempts per active eligible boss.
- A simulated ambiguous timeout never causes a duplicate verified action.
- Disabling or pausing one account immediately prevents its new mutations without affecting others.
- MCP outputs and logs contain no passwords, full session tokens, encryption keys, or TOTP secrets.

