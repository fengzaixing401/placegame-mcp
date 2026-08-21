# PlaceGame P3 WebUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the browser's root-level MCP 401 with a working setup/login WebUI while preserving independent Bearer authentication for `/mcp`.

**Architecture:** Add a small PostgreSQL-backed administrator/session service, versioned FastAPI routes, and dependency-free static assets. The existing status and idle-preview use cases are injected into the routes with a `webui:operator` actor. Health routes and WebUI routes are registered before the MCP fallback; MCP authentication remains unchanged at its own path.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy async, PostgreSQL/Alembic, argon2-cffi, vanilla HTML/CSS/JavaScript.

## Global Constraints

- Single operator with multiple game accounts; no RBAC, TOTP, recovery codes, or account creation in this milestone.
- Passwords and session tokens never enter logs, responses, fixtures, or audit payloads.
- Passwords are at least 14 characters; sessions expire after 30 minutes idle or 12 hours absolute.
- The `placegame_session` cookie is HttpOnly, SameSite=Strict, and Secure by default; tests explicitly disable Secure for HTTP clients.
- MCP Bearer authentication and WebUI cookie authentication are separate credentials.
- Use the existing application use cases for account status and idle preview.
- Keep the original `placegame-automation` worktree untouched.
- Run focused tests during implementation and one final verification gate; do not repeat unchanged suites.

---

### Task 1: Add administrator and session persistence

**Files:**
- Create: `migrations/versions/004_admin_sessions.py`
- Modify: `src/placegame/models.py`
- Test: `tests/integration/test_admin_migration.py`

**Interfaces:**
- Produces `AdminCredential` and `AdminSession` ORM records used by the auth service.

- [ ] **Step 1: Write the failing migration/model test**

Add a PostgreSQL integration test that upgrades to head, inserts one credential
and session, verifies the token digest is unique, and verifies the credential
table rejects a second singleton row.

- [ ] **Step 2: Run the focused test to verify it fails**

Run `uv run pytest tests/integration/test_admin_migration.py -q`.
Expected: collection or assertion failure because the new tables/models do not exist.

- [ ] **Step 3: Implement the migration and models**

Create `admin_credentials(id INTEGER PRIMARY KEY CHECK (id = 1), password_hash
TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)`
and `admin_sessions(id UUID PRIMARY KEY, token_digest CHAR(64) UNIQUE NOT NULL,
created_at TIMESTAMPTZ NOT NULL, absolute_expires_at TIMESTAMPTZ NOT NULL,
last_seen_at TIMESTAMPTZ NOT NULL)`. Add matching SQLAlchemy models and indexes.

- [ ] **Step 4: Run the focused test to verify it passes**

Run the same command; expected: all migration assertions pass (or the test is
explicitly skipped when PostgreSQL/Docker is unavailable).

- [ ] **Step 5: Commit**

Run `git add migrations/versions/004_admin_sessions.py src/placegame/models.py tests/integration/test_admin_migration.py` and `git commit -m "feat: add admin session persistence"`.

### Task 2: Implement password setup, login, session validation, and logout

**Files:**
- Create: `src/placegame/admin/__init__.py`
- Create: `src/placegame/admin/auth.py`
- Test: `tests/unit/test_admin_auth.py`

**Interfaces:**
- Produces `AdminAuthService.setup(password)`, `login(password)`,
  `validate(raw_token)`, and `logout(raw_token)`.
- `validate` returns an `AdminSession` record or `None` and never raises a
  credential detail error.

- [ ] **Step 1: Write the failing unit tests**

Cover one-time setup, short-password rejection, wrong-password rejection,
successful login token creation, expiry rejection, and logout revocation using
the existing async session fixture pattern.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run `uv run pytest tests/unit/test_admin_auth.py -q`.
Expected: failure because `placegame.admin.auth` is absent.

- [ ] **Step 3: Implement the minimal service**

Use `argon2.PasswordHasher` with its default Argon2id parameters. Require at
least 14 characters, generate 32 random bytes for each session, store only
`sha256(token).hexdigest()`, and set absolute expiry to `utcnow() +
timedelta(hours=12)`. Validation rejects sessions older than 30 minutes since
`last_seen_at` or the absolute expiry, then updates `last_seen_at`. Use one
transaction per operation and return generic `unauthorized` for all failed
login/validation cases.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run `uv run pytest tests/unit/test_admin_auth.py -q`; expected: all pass.

- [ ] **Step 5: Commit**

Run `git add src/placegame/admin tests/unit/test_admin_auth.py` and `git commit -m "feat: add web admin authentication"`.

### Task 3: Add versioned WebUI API routes

**Files:**
- Create: `src/placegame/admin/routes.py`
- Create: `src/placegame/admin/dependencies.py`
- Test: `tests/unit/test_admin_routes.py`
- Modify: `src/placegame/app.py`

**Interfaces:**
- Produces `APIRouter` at `/api/admin/v1` and a `require_admin` dependency.
- Route responses use camelCase JSON for existing Pydantic application models.

- [ ] **Step 1: Write failing route tests**

Using a small dependency-overridden FastAPI app, assert setup/login/logout,
missing-cookie 401, account list, account status, idle preview, and stable
error mapping. Assert the MCP child is not invoked by WebUI requests.

- [ ] **Step 2: Run tests to verify they fail**

Run `uv run pytest tests/unit/test_admin_routes.py -q`.
Expected: failure because the router and dependency do not exist.

- [ ] **Step 3: Implement routes and composition**

Create request models for passwords and a router with public auth endpoints.
Inject `AdminAuthService`, `AccountStatusQuery`, and `IdlePlanUseCase` from
`app.state`. Use actor `Actor("webui", "operator", frozenset())`, generate a
bounded UUID correlation id, and map known errors to JSON codes. Set and clear
`placegame_session` as HttpOnly, SameSite=Strict with Secure enabled by default
and a test-only explicit configuration switch.
Register the router before the MCP fallback and expose `/mcp` through the
existing bearer middleware.

- [ ] **Step 4: Run tests to verify they pass**

Run `uv run pytest tests/unit/test_admin_routes.py tests/unit/test_app_bootstrap.py -q`.
Expected: all route and bootstrap assertions pass.

- [ ] **Step 5: Commit**

Run `git add src/placegame/admin src/placegame/app.py tests/unit/test_admin_routes.py tests/unit/test_app_bootstrap.py` and `git commit -m "feat: expose web admin API"`.

### Task 4: Serve the usable root WebUI

**Files:**
- Create: `src/placegame/web/index.html`
- Create: `src/placegame/web/style.css`
- Create: `src/placegame/web/app.js`
- Modify: `src/placegame/app.py`
- Test: `tests/unit/test_webui_static.py`

**Interfaces:**
- `GET /` returns HTML; `/assets/style.css` and `/assets/app.js` return the
  bundled static files.

- [ ] **Step 1: Write the failing static tests**

Assert `/` has `text/html`, contains the login/setup shell, and assets are
served without entering the MCP bearer middleware.

- [ ] **Step 2: Run tests to verify they fail**

Run `uv run pytest tests/unit/test_webui_static.py -q`.
Expected: 404 or JSON unauthorized because no static routes exist.

- [ ] **Step 3: Implement the compact console**

Serve the three package-local files with explicit `FileResponse` routes. The
script calls only same-origin `/api/admin/v1` routes, renders setup/login and
account rows, shows loading/empty/error states, and offers status/preview
buttons. Keep controls stable and responsive without a Node build step.

- [ ] **Step 4: Run tests to verify they pass**

Run `uv run pytest tests/unit/test_webui_static.py tests/unit/test_app_bootstrap.py -q`.
Expected: all pass.

- [ ] **Step 5: Commit**

Run `git add src/placegame/web tests/unit/test_webui_static.py src/placegame/app.py` and `git commit -m "feat: serve placegame web console"`.

### Task 5: Final integration and delivery gate

**Files:**
- Modify: `Dockerfile` only if static files are excluded by packaging.
- Modify: `deploy/compose.yaml` only if the app needs an explicit web setting.
- Test: existing unit/deployment suites and a new API integration smoke test if
  the focused route tests reveal a composition gap.

- [ ] **Step 1: Run the focused backend gate**

Run `uv run pytest tests/unit/test_admin_auth.py tests/unit/test_admin_routes.py tests/unit/test_webui_static.py tests/unit/test_app_bootstrap.py -q`.

- [ ] **Step 2: Run the full required gate once**

Run `uv run pytest -q` and `uv run pyright src`.
Expected: zero failures and zero Pyright errors; integration tests may skip
only with their existing explicit Docker/database reason.

- [ ] **Step 3: Build the image**

Run `docker build -t placegame-mcp:p3 .` and verify the build exits 0.

- [ ] **Step 4: Commit any packaging fix**

If the image build exposes a packaging issue, make the smallest fix and rerun
only the affected focused test plus the image build.

- [ ] **Step 5: Handoff to Sol**

Provide the final commit, exact command output, and unresolved external
deployment notes for one read-only review before push/merge.
