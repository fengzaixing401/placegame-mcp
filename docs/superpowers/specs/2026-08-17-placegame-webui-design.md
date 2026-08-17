# PlaceGame WebUI and Public Deployment Design

**Date:** 2026-08-17

**Status:** Approved design

**Parent specification:** `2026-08-17-placegame-mcp-core-design.md`

## 1. Goal

Provide one administrator with a secure HTTPS WebUI for adding and operating multiple game accounts, reviewing scheduled work, managing inventory, configuring policies, and issuing or revoking scoped MCP tokens. The WebUI calls the same application policy engine as MCP and scheduled jobs; it does not communicate with the game directly.

## 2. Technology and Delivery

- React with TypeScript for the browser application.
- A typed JSON admin API served by the Python application.
- Server-Sent Events for job progress, alerts, and account-status refreshes.
- Static frontend assets built into the application image.
- Caddy provides public TLS, HTTP-to-HTTPS redirect, compression, and security headers.
- The application and PostgreSQL are not directly exposed to the public internet.

The interface is responsive for desktop and mobile browsers. Browser use is limited to operating this management UI; game automation remains direct HTTP only.

## 3. Authentication and Session Security

The product has one administrator identity in the first release.

### Initial setup

1. A one-time setup token is printed to the VPS console on first boot and expires after 30 minutes.
2. The administrator chooses a password of at least 14 characters.
3. The server displays a TOTP enrollment QR code and requires one valid code before setup completes.
4. Ten single-use recovery codes are generated and displayed once.
5. The setup endpoint permanently disables itself.

### Normal login

- Password hashes use Argon2id.
- A valid TOTP code is required after a correct password.
- Five failed attempts in 15 minutes impose a 15-minute account and source-IP lockout.
- Sessions use random server-side IDs in `Secure`, `HttpOnly`, `SameSite=Strict` cookies.
- Idle session expiry is 30 minutes; absolute expiry is 12 hours.
- State-changing requests require a CSRF token bound to the session.
- Password, TOTP, recovery code, and session changes require recent re-authentication.

## 4. Information Architecture

### 4.1 Dashboard

Shows one card per game account with:

- label, character, job, level, power, and current map
- online/authentication/paused state
- idle accumulation and time until collection
- bag and warehouse pressure
- next personal, map, profession, and world-boss work
- last success and current alert

Cards can be filtered by enabled, paused, warning, authentication-required, and inventory-critical state. Bulk actions are limited to pause, resume, refresh, safe maintenance, and run-now automation.

### 4.2 Account detail

Tabs:

- `Overview`: currencies, equipment summary, supplies, daily progress, and recent activity.
- `Idle`: preview, food coverage, next collection, manual collect, and history.
- `Bosses`: personal, ordinary solo, and world collaboration as distinct groups; optimizer explanation and costs are visible before execution.
- `Professions`: permanent specialization shown as immutable, progress, queue, supplies, and generated maintenance plan.
- `Inventory`: bag, warehouse, cleanup plans, protected reasons, and replacement suggestions.
- `Automation`: per-account policy and schedules.
- `Audit`: account-specific job and action history.

### 4.3 Accounts

The add-account flow supports:

- `Credentials`: username and password are tested once, encrypted, and never re-displayed.
- `Token-only`: session token is tested, encrypted, and shown with its detected expiry.

Editing credentials requires administrator re-authentication. Removing an account first disables it, waits for its active lock to drain, then deletes credentials and future jobs. Audit metadata remains with the label replaced by a tombstone identifier.

### 4.4 MCP tokens

The token page supports:

- descriptive token name
- expiry date
- allowed scopes
- all accounts or an explicit account allowlist
- creation with one-time token reveal
- last-used metadata
- immediate revocation

The UI warns when a token can perform batch or confirmation-required operations.

### 4.5 Jobs and audit

- Timeline of scheduled, MCP, and WebUI work.
- Filters for account, actor, operation, status, and correlation ID.
- Expandable plan, cost, decision reasons, retry history, and verified outcome.
- Secrets and authorization values are never available through the UI.

### 4.6 Global settings

- Scheduler health and global pause.
- Default policies inherited by new accounts.
- Account concurrency, default four.
- Data-retention settings.
- Password, TOTP, recovery-code, and administrator-session management.

## 5. Manual Action Flow

### Read-only action

The UI sends the request and displays the refreshed authoritative result.

### Low-risk mutation

1. UI asks the server to generate a plan.
2. The server returns actions, reasons, resource costs, and plan expiry.
3. The UI displays the plan and allows execution.
4. Execution returns a verified result and correlation ID.

### Confirmation-required mutation

1. The UI displays a warning with selected assets and irreversible effects.
2. The administrator explicitly confirms.
3. The UI submits the plan ID and CSRF-protected confirmation.
4. The server revalidates state under the account lock.
5. Success or rejection is shown without optimistically assuming completion.

The WebUI never offers a generic raw endpoint or arbitrary request-body editor.

## 6. Policy Editor

Policies are edited through typed controls, not free-form JSON by default.

- Idle collection threshold: allowed range 1 hour through server capacity minus 10 minutes; default 11.5 hours.
- Boss minimum chance: allowed range 50-98; default 80.
- Personal paid attempts: default off.
- World collaboration: enabled and three attempts for every eligible active boss by the confirmed design.
- Material reserves: non-negative integer; default 64 per boss material.
- Profession stock targets and queue horizon.
- Inventory warning and critical thresholds; defaults 85% and 95%.
- Automatic equipment quality ceiling: white, green, or blue only; default blue.
- Safe reward claim toggle: default on.

The save screen presents a before/after diff. Server validation rejects impossible combinations, unknown fields, and an inventory automatic-quality ceiling above blue.

## 7. Live Updates

- A session-authenticated SSE connection publishes sanitized account snapshots, job transitions, and alerts.
- Events carry monotonically increasing IDs so the browser can resume after reconnect.
- The browser falls back to 30-second polling if SSE is unavailable.
- Sensitive mutation outcomes remain accessible through authenticated detail requests rather than being embedded in broad broadcast events.

## 8. Public HTTPS Security

Caddy and the application enforce:

- TLS 1.2 or newer and automatic certificate renewal.
- Strict Transport Security after initial domain validation.
- Content Security Policy that permits only application assets and required same-origin connections.
- `X-Content-Type-Options: nosniff`, restrictive referrer policy, and frame denial.
- Request-body limits for all public endpoints.
- Separate rate limits for login, MCP, reads, and mutations.
- No direct PostgreSQL port exposure.

MCP uses bearer authentication on `/mcp`. WebUI session cookies are not accepted as MCP authentication, and MCP tokens are not accepted for administrator pages.

## 9. Admin API Shape

The WebUI uses versioned same-origin routes under `/api/admin/v1`:

- `/auth/*`
- `/accounts/*`
- `/accounts/{id}/status`
- `/accounts/{id}/plans/*`
- `/accounts/{id}/actions/*`
- `/accounts/{id}/inventory/*`
- `/accounts/{id}/automation/*`
- `/jobs/*`
- `/audit/*`
- `/mcp-tokens/*`
- `/settings/*`
- `/events`

Mutation routes accept idempotency keys. Error responses use stable codes, a safe user message, correlation ID, and optional structured field errors. They do not include Python traces or upstream raw bodies.

## 10. Visual and Interaction Rules

- Account context is always visible on mutation pages.
- Batch actions show the exact selected account count and labels.
- Read-only, automatic-safe, confirmation-required, paused, and forbidden states use both text and icon cues, never color alone.
- Costs show owned amount, action cost, configured reserve, and projected remainder.
- Optimizer decisions show chosen difficulty, skills, tactic, affix, chance, material boost, potion, and rejected alternatives in a compact explanation.
- Dates and schedules are displayed in Beijing time with an explicit `UTC+8` label.
- Destructive confirmation controls cannot be triggered by pressing Enter on initial page load.

## 11. Failure and Recovery UX

- Authentication-required accounts show a dedicated recovery action.
- Server conflict invalidates the current plan and offers regeneration.
- Network loss never displays an unverified mutation as successful.
- A global pause banner identifies whether it was manual, schema-protection, or update-required.
- Critical inventory pressure and missed world-boss participation remain visible until acknowledged, even after the immediate condition clears.
- The UI shows partial success per account for batch work.

## 12. Testing Strategy

- Component tests for policy forms, costs, protection badges, and confirmation dialogs.
- Admin API tests for sessions, TOTP, CSRF, lockout, scope separation, and secret redaction.
- End-to-end tests against the fake game server for account addition, idle collection, boss planning, inventory cleanup, job history, and MCP token lifecycle.
- Accessibility checks for keyboard navigation, labels, focus order, contrast, and live updates.
- Responsive tests at mobile and desktop widths.
- Security tests for cookie flags, CSP, rate limits, IDOR between game account IDs, and plan ownership.

WebUI tests may automate this management interface. They never drive or inspect the PlaceGame website.

## 13. Acceptance Criteria

- The administrator can add credential and token-only game accounts and operate them independently.
- TOTP is mandatory after initial setup, and recovery codes are single-use.
- A token restricted to one account cannot read or mutate another account.
- WebUI, MCP, and scheduler actions produce the same policy decisions for identical state.
- Manual high-value inventory actions cannot execute without an unexpired confirmed plan.
- Account, job, and alert views remain usable on desktop and mobile.
- The public deployment passes the documented TLS, cookie, CSRF, CSP, rate-limit, and secret-redaction checks.
