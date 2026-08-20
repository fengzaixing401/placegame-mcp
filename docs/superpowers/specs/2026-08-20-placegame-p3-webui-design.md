# PlaceGame P3 WebUI Login And Idle Console Design

**Date:** 2026-08-20

**Status:** Approved under the operator's standing instruction to use the
recommended implementation decisions.

## Problem

The current application mounts the MCP ASGI child at `/`. A browser request to
`/` therefore enters `StaticBearerAuthMiddleware` without an Authorization
header and receives `{"error":"unauthorized"}`. There is no browser login API
or static WebUI in the image.

## Scope

This milestone is one small, usable vertical slice for a single operator who
may manage multiple game accounts:

- first-run administrator password setup;
- password login, logout, and a persistent server-side session;
- a real `/` page served by the application;
- account list, authoritative account status refresh, and idle preview;
- a responsive, compact operations console for desktop and mobile browsers.

It does not add RBAC, multiple administrators, TOTP, recovery codes, account
creation, game mutation controls, scheduler behavior, or arbitrary game API
access. Those are separate milestones.

## Authentication Boundary

The administrator credential is stored as an Argon2id password hash in a
singleton PostgreSQL row. On a fresh database, `POST /api/admin/v1/auth/setup`
accepts a password once and creates that row; subsequent setup attempts return
`setup_already_complete`. Passwords are never logged or returned.

Successful login creates a random opaque session token. Only its SHA-256 digest
is stored in PostgreSQL. The token is sent as an HttpOnly, SameSite=Lax cookie
named `placegame_session`, with a 30-day expiry. Logout deletes the session and
clears the cookie. The cookie is not accepted by MCP. Same-origin JSON routes
and SameSite cookies are sufficient for this single-operator milestone; no
cross-origin API is enabled.

`GET /api/admin/v1/auth/status` is public and returns whether setup is needed
and whether the current cookie is authenticated. Protected routes return the
stable body `{"error":"unauthorized"}` with HTTP 401.

## HTTP Surface

Public:

- `GET /api/admin/v1/auth/status`
- `POST /api/admin/v1/auth/setup` with `{password}`
- `POST /api/admin/v1/auth/login` with `{password}`
- `GET /health/live`
- `GET /health/ready`
- `/` and bundled static assets

Authenticated:

- `POST /api/admin/v1/auth/logout`
- `GET /api/admin/v1/accounts`
- `GET /api/admin/v1/accounts/{account_id}/status`
- `GET /api/admin/v1/accounts/{account_id}/idle-preview`

MCP remains a separate Bearer-protected endpoint at `/mcp`. A WebUI cookie
cannot authenticate it, and an MCP token cannot authenticate WebUI APIs.

Application errors are translated to stable JSON codes. Account not found is
404, disabled/paused or invalid preview conditions are 409, upstream/game
errors use the existing stable application mapping, and unexpected failures
are 500 without traces or secret values.

## Composition

`placegame.admin.auth` owns password hashing, session token generation, and
database access. `placegame.admin.routes` owns request validation and response
mapping. The existing `AccountStatusQuery` and `IdlePlanUseCase` remain the
only source of account/status/preview behavior. WebUI routes create an actor
`webui:operator` and never call the game client directly.

The FastAPI composition root registers health routes, WebUI API routes, and a
static file fallback first. The MCP child is mounted only as the final
fallback, preserving `/mcp` authentication while allowing `/` to serve HTML.

## UI

The image contains a small dependency-free HTML/CSS/JavaScript console under
`src/placegame/web`. It starts on the setup or login form, then shows account
rows with status and idle preview actions. Loading, empty, unauthorized, and
error states are explicit. The layout uses restrained colors, stable controls,
and no marketing content; it is usable without a separate Node build step.

## Data And Migration

Migration `004_admin_sessions` creates:

- `admin_credentials` (singleton id, Argon2id hash, timestamps);
- `admin_sessions` (UUID, token digest, created/expiry/last-used timestamps,
  unique digest).

Expired sessions are ignored and removed during authentication. No plaintext
password or session token is persisted.

## Verification

Focused tests prove setup is one-time, wrong passwords return 401, login sets
the HttpOnly cookie, logout revokes it, protected APIs reject missing cookies,
the root page is HTML, `/mcp` still rejects missing Bearer credentials, and
the account/status/preview routes delegate to the existing use cases. A final
full test gate and Docker image build run after the focused cycle.
