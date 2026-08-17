# PlaceGame WebUI and Edge Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a secure, responsive administrator WebUI for managing multiple PlaceGame accounts, policies, jobs, inventory plans, and scoped MCP tokens through the same policy engine used by the scheduler and MCP.

**Architecture:** FastAPI serves versioned same-origin admin routes and a session-authenticated SSE stream; React/TypeScript builds static assets for the final application image. Server-side sessions, Argon2id, mandatory TOTP, CSRF, idempotency, and re-authentication protect every state-changing route. The deployment plan integrates the built assets with the loopback-only production image and existing 1Panel OpenResty edge; the browser never contacts PlaceGame directly.

**Tech Stack:** Python 3.12, FastAPI/Pydantic 2/SQLAlchemy async from the core plan, argon2-cffi, pyotp, qrcode, React 18, TypeScript, Vite, TanStack Query, React Router, Vitest, Testing Library, Playwright for management-UI tests only, and PostgreSQL 16.

## Global Constraints

- The first release has one administrator identity; it supports multiple independent game accounts and is not a multi-tenant end-user product.
- A one-time setup token is printed to the VPS console on first boot, expires after 30 minutes, and permanently disables setup after successful password plus TOTP enrollment.
- The administrator password is at least 14 characters and is hashed with Argon2id; a valid TOTP code is required after every correct password.
- Ten single-use recovery codes are generated and displayed once. Five failed attempts in 15 minutes impose a 15-minute account and source-IP lockout.
- Sessions use random server-side IDs in `Secure`, `HttpOnly`, `SameSite=Strict` cookies with 30-minute idle and 12-hour absolute expiry.
- State-changing requests require a CSRF token bound to the session; password, TOTP, recovery-code, and session changes require recent re-authentication.
- Recent re-authentication means both the administrator password and a TOTP code were verified within the previous ten minutes.
- Before login, `/auth/status` creates a short-lived pre-authentication session and CSRF token; setup and login rotate that session rather than exempting authentication routes from CSRF.
- The UI calls only versioned same-origin routes under `/api/admin/v1`; it never sends game endpoint paths or arbitrary request bodies.
- Credential-mode game usernames/passwords are tested once, encrypted, and never re-displayed; token-only game tokens are tested, encrypted, and shown only with detected expiry.
- Account removal first disables automation, waits for the active lock to drain, deletes credentials and future jobs, and keeps audit metadata under a tombstone identifier.
- Low-risk mutations use a server-generated plan and verified result; confirmation-required mutations require an explicit second confirmation and never use optimistic success.
- Every mutation page keeps the account label/character visible; batch actions show the exact selected count and labels.
- The policy editor uses typed controls, shows a before/after diff, rejects unknown fields and an automatic equipment quality ceiling above blue, and displays schedules in Beijing time with `UTC+8`.
- Server-Sent Events (SSE) are sanitized, carry monotonically increasing IDs, support `Last-Event-ID` resume, and fall back to 30-second polling; sensitive mutation details require an authenticated detail request.
- The application enforces CSP, `nosniff`, restrictive referrer policy, frame denial, request limits, and separate rate limits for login, MCP, reads, and mutations; the later operator-managed 1Panel edge adds TLS 1.2+, redirects, and HSTS after domain validation.
- PostgreSQL is never public and the Singapore application remains bound to `127.0.0.1:18080` until the operator configures 1Panel; WebUI cookies are not accepted as MCP credentials, and MCP bearer tokens are not accepted for administrator pages.
- Management UI tests may use Playwright only against the isolated admin app and fake game server; no test or production browser path drives or inspects the PlaceGame website.

---

## Execution Order

Execute this plan only after the Core and Inventory plans and their acceptance suites pass. It consumes their frozen service contracts and completes the application surface without changing game-operation semantics. The complete order is deployment repository bootstrap → Core → Inventory → WebUI → remaining deployment tasks.

## File Map

- Modify: `src/placegame/models.py` — add administrator, setup, login-challenge, session, CSRF, lockout, recovery-code, idempotency, rate-limit, and SSE event models.
- Modify: `pyproject.toml`, `uv.lock` — add the pinned QR dependency and preserve the Python lock.
- Create: `migrations/versions/003_admin.py` — admin/security schema and indexes.
- Create: `src/placegame/admin/__init__.py`, `src/placegame/admin/routes/__init__.py`, `src/placegame/admin/errors.py`, `src/placegame/admin/schemas.py` — stable API envelopes and typed request/response models.
- Create: `src/placegame/admin/auth.py` — setup, password/TOTP/recovery authentication, Argon2id, and lockout.
- Create: `src/placegame/admin/sessions.py`, `src/placegame/admin/csrf.py`, `src/placegame/admin/dependencies.py`, `src/placegame/admin/rate_limits.py` — server sessions, CSRF binding, recent re-auth, route dependencies, and separate public request buckets.
- Create: `src/placegame/admin/routes/auth.py`, `src/placegame/admin/routes/accounts.py`, `src/placegame/admin/routes/actions.py`, `src/placegame/admin/routes/inventory.py`, `src/placegame/admin/routes/jobs.py`, `src/placegame/admin/routes/audit.py`, `src/placegame/admin/routes/tokens.py`, `src/placegame/admin/routes/settings.py`, `src/placegame/admin/routes/events.py` — `/api/admin/v1` route modules.
- Create: `src/placegame/admin/app.py` — router composition, idempotency middleware, correlation IDs, and sanitized exception mapping.
- Create: `src/placegame/admin/events.py` — durable sanitized event publication and replay service.
- Modify: `src/placegame/app.py` — mount admin routes, SSE, and built static assets without weakening `/mcp` authentication.
- Create: `web/package.json`, `web/package-lock.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/playwright.config.ts`, `web/index.html` — frontend build and test configuration.
- Create: `web/src/main.tsx`, `web/src/router.tsx`, `web/src/api/client.ts`, `web/src/api/types.ts`, `web/src/api/query.ts` — typed client, route shell, and cache.
- Create: `web/src/auth/AuthProvider.tsx`, `web/src/auth/ProtectedRoute.tsx`, `web/src/auth/LoginPage.tsx`, `web/src/auth/SetupPage.tsx` — setup/login/recovery flows.
- Create: `web/src/components/AccountCard.tsx`, `web/src/components/StatusBadge.tsx`, `web/src/components/PlanPreview.tsx`, `web/src/components/ConfirmDialog.tsx`, `web/src/components/ProtectionBadge.tsx`, `web/src/components/EventStream.tsx` — reusable accessible controls.
- Create: `web/src/pages/DashboardPage.tsx`, `web/src/pages/AccountPage.tsx`, `web/src/pages/AccountsPage.tsx`, `web/src/pages/JobsPage.tsx`, `web/src/pages/AuditPage.tsx`, `web/src/pages/McpTokensPage.tsx`, `web/src/pages/SettingsPage.tsx` — information architecture pages.
- Create: `web/src/styles.css` — responsive layout, text/icon state cues, focus styles, and contrast tokens.
- Modify: `.env.example`, `src/placegame/app.py` — static serving and application security-header defaults; production image, Compose, and external edge integration belong to the deployment plan.
- Create: `tests/admin/conftest.py`, `tests/admin/fake_clock.py` — session, TOTP, and API fixtures.
- Create: `tests/admin/test_auth.py`, `tests/admin/test_sessions.py`, `tests/admin/test_api_contract.py`, `tests/admin/test_events.py`, `tests/admin/test_security.py` — backend security and route tests.
- Create: `web/src/auth/AuthProvider.test.tsx`, `web/src/pages/AccountsPage.test.tsx`, `web/src/pages/SettingsPage.test.tsx`, `web/src/components/AccountCard.test.tsx`, `web/src/components/PlanPreview.test.tsx`, `web/src/components/EventStream.test.tsx` — component/accessibility tests.
- Create: `web/e2e/admin-flow.spec.ts` — management-UI end-to-end flow against the fake game server.
- Create: `tests/admin/e2e_server.py` — isolated ASGI/fake-game E2E server that serves the production frontend bundle on port 4173.
- Create: `tests/admin/test_acceptance.py` — account, inventory, policy, token, and deployment acceptance checks.

## Cross-Plan API Contracts

Every admin response uses this envelope, allowing the frontend and future clients to handle errors without Python details:

```python
class ApiError(BaseModel):
    code: str
    message: str
    correlation_id: str
    field_errors: dict[str, str] = Field(default_factory=dict)

class ApiResponse(BaseModel, Generic[T]):
    data: T
    correlation_id: str
```

Routes are fixed as follows: `/api/admin/v1/auth/{setup,status,csrf,password,totp,recovery,logout,reauth,me}`, `/accounts`, `/accounts/{id}/{status,plans,actions,inventory,automation}`, `/jobs`, `/audit`, `/mcp-tokens`, `/settings`, and `/events`. Mutations require an `Idempotency-Key` and `X-CSRF-Token` header; responses include `correlation_id`.

Authentication methods are exact: `GET /auth/status` creates/refreshes the pre-auth session, `GET /auth/csrf` returns its bound CSRF value, `POST /auth/setup` begins enrollment, `PUT /auth/setup` completes enrollment, `POST /auth/password` creates a five-minute challenge, `POST /auth/totp` or `/auth/recovery` consumes that challenge, `POST /auth/reauth` verifies password plus TOTP, `POST /auth/logout` revokes the current session, and `GET /auth/me` returns sanitized session metadata.

DTO ownership is also fixed: Task 2 adds `SetupState`, `LoginChallengeView`, and `AdminSessionView`; Task 3 adds `CsrfView` and `ReauthRequest`; Task 4 adds `AddAccountRequest`, `UpdateAccountRequest`, `AccountView`, `CreateMcpTokenRequest`, `McpTokenMetadataView`, and `IssuedMcpTokenView`; Task 5 adds `DashboardView`, `PolicyUpdate`, `PolicyDiff`, `AllowedAdminAction`, `CreatePlanRequest`, `ExecutePlanRequest`, `ActionPlanView`, `VerifiedActionResult`, and `BatchActionResult`; Task 7 adds `ChangePasswordRequest`, `RotateTotpRequest`, `RecoveryCodesView`, and `RevokeSessionsRequest`. These Pydantic models live in `src/placegame/admin/schemas.py`; route modules do not return untyped dictionaries.

### Task 1: Bootstrap the Admin API and Frontend Build

**Files:**
- Create: `src/placegame/admin/__init__.py`
- Create: `src/placegame/admin/routes/__init__.py`
- Create: `src/placegame/admin/errors.py`
- Create: `src/placegame/admin/schemas.py`
- Create: `src/placegame/admin/app.py`
- Modify: `src/placegame/app.py`
- Create: `web/package.json`
- Create: `web/package-lock.json`
- Create: `web/tsconfig.json`
- Create: `web/vite.config.ts`
- Create: `web/playwright.config.ts`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/api/client.ts`
- Create: `web/src/api/types.ts`
- Create: `web/src/api/query.ts`
- Create: `tests/admin/conftest.py`
- Test: `tests/admin/test_api_contract.py`

**Interfaces:**
- Produces `admin_router`, `ApiResponse[T]`, `ApiError`, `AdminClient.request`, and a Vite build that emits `web/dist` for the Python image.

- [ ] **Step 1: Write failing API-envelope and build tests**

```python
async def test_admin_error_has_stable_code_and_correlation_id(admin_client):
    response = await admin_client.get("/api/admin/v1/_contract/error")
    assert response.status_code == 401
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "authentication_required"
    assert body["error"]["correlation_id"]

def test_frontend_build_has_entrypoint():
    subprocess.run(["npm", "run", "build"], cwd="web", check=True)
    assert Path("web/dist/index.html").exists()
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/admin/test_api_contract.py::test_admin_error_has_stable_code_and_correlation_id -q; npm --prefix web test -- --run`

Expected: FAIL because the admin router and `web` package do not exist.

- [ ] **Step 3: Add typed backend envelope and React/Vite shell**

```json
{
  "name": "placegame-admin",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite", "build": "tsc -b && vite build", "typecheck": "tsc -b --pretty false",
    "test": "vitest", "test:e2e": "playwright test"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.62.0", "lucide-react": "^0.468.0", "react": "^18.3.1", "react-dom": "^18.3.1", "react-router-dom": "^6.28.0"
  },
  "devDependencies": {
    "@axe-core/react": "^4.10.2", "@playwright/test": "^1.49.0", "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.1.0", "@testing-library/user-event": "^14.5.2", "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1", "@vitejs/plugin-react": "^4.3.4", "jsdom": "^25.0.1",
    "typescript": "^5.7.2", "vite": "^6.0.1", "vitest": "^2.1.8"
  }
}
```

```python
# src/placegame/admin/app.py
router = APIRouter(prefix="/api/admin/v1")

async def admin_error_handler(request: Request, exc: AdminError):
    correlation_id = request.state.correlation_id
    return JSONResponse(status_code=exc.status, content={"error": ApiError(code=exc.code, message=exc.safe_message, correlation_id=correlation_id, field_errors=exc.fields).model_dump()})

def install_admin(app: FastAPI) -> None:
    app.include_router(router)
    app.add_exception_handler(AdminError, admin_error_handler)
```

```typescript
// web/src/api/client.ts
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init, headers: { Accept: "application/json", ...init.headers } });
  const body = await response.json();
  if (!response.ok) throw new AdminApiError(body.error.code, body.error.message, body.error.correlation_id, body.error.field_errors);
  return body.data as T;
}
```

```typescript
// web/playwright.config.ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://127.0.0.1:4173" },
  webServer: {
    command: "uv run --project .. python ../tests/admin/e2e_server.py",
    url: "http://127.0.0.1:4173/health/live",
    reuseExistingServer: false
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 } } }
  ]
});
```

`tests/admin/conftest.py` builds an isolated admin app and mounts `/api/admin/v1/_contract/error` only on that test app; the probe raises a sanitized `AdminError` and is never imported by production composition. `web/package.json` pins React, React Router, TanStack Query, Lucide, TypeScript, Vite, Vitest, Testing Library, axe, and Playwright; scripts are `dev`, `build`, `test`, `test:e2e`, and `typecheck`. `web/playwright.config.ts` defines `desktop` at 1440×900 and `mobile` at 390×844, both targeting only the management app/fake game stack. Run `npm install --package-lock-only`, then `npm ci`, and commit `web/package-lock.json`. The initial React entry renders a route outlet and an explicit loading/failure state; it does not assume authentication.

- [ ] **Step 4: Run backend and frontend checks**

Run: `uv run pytest tests/admin/test_api_contract.py::test_admin_error_has_stable_code_and_correlation_id -q && npm --prefix web ci && npm --prefix web run typecheck && npm --prefix web run build`

Expected: backend test passes, TypeScript reports zero errors, and Vite creates `web/dist/index.html`.

- [ ] **Step 5: Commit the admin-shell checkpoint**

```bash
git add src/placegame/admin src/placegame/app.py web tests/admin/conftest.py tests/admin/test_api_contract.py
git commit -m "feat: bootstrap typed admin api and web shell"
```

### Task 2: Implement One-Time Setup, Password/TOTP Login, and Recovery

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/placegame/models.py`
- Create: `migrations/versions/003_admin.py`
- Modify: `src/placegame/admin/schemas.py`
- Create: `src/placegame/admin/auth.py`
- Create: `src/placegame/admin/routes/auth.py`
- Modify: `src/placegame/admin/app.py`
- Create: `web/src/auth/SetupPage.tsx`
- Create: `web/src/auth/LoginPage.tsx`
- Create: `web/src/auth/AuthProvider.tsx`
- Modify: `tests/admin/conftest.py`
- Create: `tests/admin/fake_clock.py`
- Test: `tests/admin/test_auth.py`
- Test: `web/src/auth/AuthProvider.test.tsx`

**Interfaces:**
- Produces `AdminAuthService.begin_setup`, `complete_setup`, `verify_password`, `verify_totp`, `redeem_recovery_code`, and `verify_password_and_totp`, plus auth routes returning `SetupState`, `LoginChallengeView`, or `AdminSessionView`.

- [ ] **Step 1: Write failing setup/login/recovery tests**

```python
async def test_setup_requires_password_and_valid_totp_then_disables_it(auth_service):
    setup = await auth_service.begin_setup(console_token=auth_service.console_token)
    assert setup.expires_at - utcnow() <= timedelta(minutes=30)
    with pytest.raises(InvalidSetup):
        await auth_service.complete_setup(setup.id, setup.token, "short", "000000")
    result = await auth_service.complete_setup(setup.id, setup.token, "a" * 14, totp_code_for_setup(setup.id))
    assert len(result.recovery_codes) == 10
    with pytest.raises(SetupDisabled):
        await auth_service.begin_setup(console_token=setup.token)

async def test_password_then_totp_and_recovery_lockout(auth_service):
    recovery_codes = await auth_service.seed_admin("a" * 14, "totp-secret")
    challenge = await auth_service.verify_password("a" * 14, source_ip="203.0.113.10")
    assert challenge.requires_totp is True
    session = await auth_service.verify_totp(challenge.id, totp("totp-secret"), source_ip="203.0.113.10")
    assert session.authenticated is True
    recovery_challenge = await auth_service.verify_password("a" * 14, source_ip="203.0.113.10")
    assert (await auth_service.redeem_recovery_code(recovery_challenge.id, recovery_codes[0], source_ip="203.0.113.10")).authenticated
    with pytest.raises(InvalidRecoveryCode):
        await auth_service.redeem_recovery_code(recovery_challenge.id, recovery_codes[0], source_ip="203.0.113.10")

async def test_five_failures_in_fifteen_minutes_lock_account_and_ip(auth_service, fake_clock):
    await auth_service.seed_admin("a" * 14, "totp-secret")
    for _ in range(5):
        with pytest.raises(AuthenticationRequired):
            await auth_service.verify_password("wrong-password", source_ip="203.0.113.11")
    with pytest.raises(LockedOut):
        await auth_service.verify_password("a" * 14, source_ip="203.0.113.11")
    fake_clock.advance(minutes=15, seconds=1)
    assert (await auth_service.verify_password("a" * 14, source_ip="203.0.113.11")).requires_totp
```

- [ ] **Step 2: Run auth tests and verify missing-service failure**

Run: `uv run pytest tests/admin/test_auth.py -q`

Run: `npm --prefix web test -- --run src/auth/AuthProvider.test.tsx`

Expected: FAIL because admin tables, setup service, and auth routes are absent.

- [ ] **Step 3: Implement Argon2id, TOTP enrollment, recovery, and lockout**

Add `qrcode[pil]>=7.4,<9` to `pyproject.toml` and refresh `uv.lock`. Migration `003_admin` creates `admin_identity`, `admin_setup`, `admin_login_challenges`, `admin_sessions`, `admin_recovery_codes`, `admin_lockouts`, `admin_idempotency`, `admin_rate_limits`, and `admin_events` with expiry/principal/sequence indexes. Store a random setup token digest with a 30-minute expiry and print the one-time clear token only through the console logger. `begin_setup` verifies that token, generates a fresh base32 TOTP secret server-side, stores it encrypted, and returns only the provisioning URI plus rendered QR; the browser never submits or chooses the secret. `complete_setup` validates a password of at least 14 characters and a code against the stored secret, Argon2id-hashes the password, generates ten random single-use recovery-code hashes, and marks setup disabled in one transaction. The response contains QR/recovery values once; subsequent reads return neither.

```python
async def verify_password(self, password: str, source_ip: str) -> LoginChallenge:
    if await self.lockouts.is_locked(source_ip):
        raise AdminError("locked_out", "try again later", 429)
    admin = await self.repo.admin()
    if admin is None or not self.hasher.verify(admin.password_hash, password):
        await self.lockouts.record_failure(source_ip)
        raise AdminError("authentication_required", "invalid credentials", 401)
    challenge = await self.sessions.create_login_challenge(admin.id, source_ip, expires=timedelta(minutes=5))
    return LoginChallenge(id=challenge.id, requires_totp=True)
```

`verify_totp` consumes only a live challenge, accepts a valid six-digit code or one single-use recovery code on the recovery route, and creates a random server-side session ID. `verify_password_and_totp` validates both factors in one call for Task 3 re-authentication and does not create a second administrator identity. Five failures in 15 minutes lock both administrator and source IP for 15 minutes. The React flow never stores password, TOTP, or recovery codes in local storage and announces validation errors through an `aria-live` region.

- [ ] **Step 4: Run auth/API and component tests**

Run: `uv run pytest tests/integration/test_migrations.py tests/admin/test_auth.py -q && npm --prefix web test -- --run src/auth/AuthProvider.test.tsx`

Expected: the Testcontainers migration reaches the admin head and all setup, TOTP, recovery, expiry, lockout, and form-state tests pass.

- [ ] **Step 5: Commit the authentication checkpoint**

```bash
git add pyproject.toml uv.lock src/placegame/models.py migrations/versions/003_admin.py src/placegame/admin/schemas.py src/placegame/admin/auth.py src/placegame/admin/routes/auth.py src/placegame/admin/app.py web/src/auth tests/admin/conftest.py tests/admin/fake_clock.py tests/admin/test_auth.py
git commit -m "feat: secure admin setup and totp authentication"
```

### Task 3: Add Server Sessions, CSRF, Re-authentication, and Idempotency

**Files:**
- Create: `src/placegame/admin/sessions.py`
- Create: `src/placegame/admin/csrf.py`
- Create: `src/placegame/admin/dependencies.py`
- Create: `src/placegame/admin/rate_limits.py`
- Modify: `src/placegame/admin/schemas.py`
- Modify: `src/placegame/admin/app.py`
- Modify: `web/src/api/client.ts`
- Modify: `tests/admin/conftest.py`
- Create: `tests/admin/test_sessions.py`
- Create: `tests/admin/test_security.py`

**Interfaces:**
- Produces `AdminSession`, `SessionService.create_pre_authenticated/rotate_authenticated/mark_reauthenticated/revoke/validate`, `require_admin`, `require_csrf`, `require_recent_reauth`, `IdempotencyMiddleware`, `RateLimitMiddleware`, `CsrfView`, `ReauthRequest`, and client helpers that attach CSRF/idempotency headers to every mutation.

- [ ] **Step 1: Write failing cookie/CSRF/reauth tests**

```python
async def test_session_expiry_cookie_flags_and_csrf(admin_client, fake_clock):
    await login(admin_client)
    response = await admin_client.post("/api/admin/v1/_contract/mutation", json={"value": 1})
    assert response.status_code == 403 and response.json()["error"]["code"] == "csrf_required"
    token = await admin_client.get_csrf()
    fake_clock.advance(minutes=11)
    response = await admin_client.post("/api/admin/v1/_contract/requires-reauth", headers={"X-CSRF-Token": token, "Idempotency-Key": "k1"}, json={"value": 1})
    assert response.status_code == 401 and response.json()["error"]["code"] == "reauth_required"
    assert admin_client.cookie("placegame_session").secure is True
    fake_clock.advance(hours=12, seconds=1)
    assert (await admin_client.get("/api/admin/v1/auth/me")).status_code == 401

async def test_pre_authentication_routes_also_require_bound_csrf(admin_client):
    status = await admin_client.get("/api/admin/v1/auth/status")
    assert status.json()["data"]["csrf_token"]
    denied = await admin_client.post("/api/admin/v1/auth/password", json={"password": "a" * 14})
    assert denied.status_code == 403

async def test_idempotency_replays_same_mutation_result(admin_client):
    await login_and_reauth(admin_client)
    first = await admin_client.post("/api/admin/v1/_contract/mutation", headers=mutation_headers("same"), json={"value": 1})
    second = await admin_client.post("/api/admin/v1/_contract/mutation", headers=mutation_headers("same"), json={"value": 1})
    assert second.json() == first.json()
```

- [ ] **Step 2: Run security tests and verify they fail**

Run: `uv run pytest tests/admin/test_sessions.py tests/admin/test_security.py -q`

Expected: FAIL because session, CSRF, and idempotency dependencies are not installed.

- [ ] **Step 3: Implement server-side session and request guards**

Persist only a SHA-256 digest of the random session ID, `created_at`, `last_seen_at`, `absolute_expires_at`, `reauthenticated_at`, user agent hash, and source-IP hash. `GET /auth/status` creates a five-minute pre-authentication session and returns its CSRF token; setup/password/TOTP requests require that token, and successful login rotates it into an authenticated session. Set every cookie as `Secure; HttpOnly; SameSite=Strict`, rotate it after login and re-auth, expire authenticated sessions at 30 minutes idle or 12 hours absolute, and revoke on logout. Generate a session-bound CSRF token, store its digest, and compare with `secrets.compare_digest` on every non-GET request.

```python
async def require_recent_reauth(request: Request, session: AdminSession = Depends(require_admin)) -> AdminSession:
    if session.reauthenticated_at is None or utcnow() - session.reauthenticated_at > timedelta(minutes=10):
        raise AdminError("reauth_required", "confirm your password and TOTP again", 401)
    return session
```

`tests/admin/conftest.py` adds mutation and recent-reauth probes to the isolated test app only, allowing this checkpoint to prove middleware behavior before Task 4 creates account routes. `IdempotencyMiddleware` requires `Idempotency-Key` on mutations, stores the request hash and sanitized response per session/route/key, returns the stored response for an identical retry, and returns `idempotency_key_reused` for a different body. The TypeScript client first fetches `/auth/csrf`, adds `X-CSRF-Token` and a UUID idempotency key, and treats every network-disconnected mutation as unknown until a detail refresh.

```python
RATE_BUCKETS = {
    "login": Rate(5, timedelta(minutes=15)),
    "mcp": Rate(120, timedelta(minutes=1)),
    "read": Rate(300, timedelta(minutes=1)),
    "mutation": Rate(60, timedelta(minutes=1)),
}
```

`RateLimitMiddleware` selects one fixed bucket from the normalized route template, keys login by administrator plus source IP and other routes by authenticated principal, returns `429 rate_limited` with `Retry-After`, and stores counters in PostgreSQL so restarts do not reset protection. The re-auth route verifies both password and TOTP and sets `reauthenticated_at`; password, TOTP, recovery-code regeneration, credential edits, and session revocation depend on `require_recent_reauth`.

- [ ] **Step 4: Run session/security tests**

Run: `uv run pytest tests/admin/test_sessions.py tests/admin/test_security.py -q`

Expected: cookie flags, idle/absolute expiry, CSRF binding, re-auth, lockout interaction, replay, and cross-session tests pass.

- [ ] **Step 5: Commit the request-security checkpoint**

```bash
git add src/placegame/admin/sessions.py src/placegame/admin/csrf.py src/placegame/admin/dependencies.py src/placegame/admin/rate_limits.py src/placegame/admin/schemas.py src/placegame/admin/app.py web/src/api/client.ts tests/admin/conftest.py tests/admin/test_sessions.py tests/admin/test_security.py
git commit -m "feat: protect admin mutations with sessions csrf and idempotency"
```

### Task 4: Implement Account and MCP Token Administration

**Files:**
- Modify: `src/placegame/admin/schemas.py`
- Create: `src/placegame/admin/routes/accounts.py`
- Create: `src/placegame/admin/routes/tokens.py`
- Modify: `src/placegame/admin/app.py`
- Create: `web/src/pages/AccountsPage.tsx`
- Create: `web/src/pages/McpTokensPage.tsx`
- Create: `web/src/components/ConfirmDialog.tsx`
- Test: `tests/admin/test_api_contract.py`
- Test: `web/src/pages/AccountsPage.test.tsx`

**Interfaces:**
- Produces credential/token-only account add/edit/disable/pause/resume/remove routes using the frozen core `AccountService`; MCP token list/get/create/rotate/revoke routes using the frozen core `McpTokenService`; and the `AddAccountRequest`, `UpdateAccountRequest`, `AccountView`, `CreateMcpTokenRequest`, `McpTokenMetadataView`, and one-time `IssuedMcpTokenView` DTOs.

- [ ] **Step 1: Write failing account/token route tests**

```python
async def test_add_credential_account_never_returns_password(admin_client):
    await login_and_reauth(admin_client)
    response = await admin_client.post("/api/admin/v1/accounts", headers=mutation_headers("add-a"), json={"label": "A", "auth_mode": "credentials", "game_username": "u", "game_password": "p"})
    assert response.status_code == 201
    assert "game_password" not in response.json()["data"]

async def test_mcp_token_is_revealed_once_and_revocation_is_immediate(admin_client):
    token = await create_token(admin_client, scopes=["game:read"], account_ids=[str(A_ID)])
    assert token["secret"].startswith("pgm_")
    assert (await admin_client.get(f"/api/admin/v1/mcp-tokens/{token['id']}")).json()["data"].get("secret") is None
    await revoke_token(admin_client, token["id"])
    assert await mcp_verify(token["secret"]) == "invalid_token"
```

- [ ] **Step 2: Run tests and verify route/service failure**

Run: `uv run pytest tests/admin/test_api_contract.py -q`

Expected: FAIL because account/token routes and pages are not registered.

- [ ] **Step 3: Implement account lifecycle and scoped token routes**

Account creation accepts either `{auth_mode:"credentials", game_username, game_password}` or `{auth_mode:"token_only", session_token}` (never both), tests the credential with `bootstrap`, encrypts the secret through the core `SecretBox`, and returns a sanitized snapshot. Label edits call `AccountService.update_label`; credential-mode edits call `update_credentials`; token-only edits call `update_token_only`; status routes call `enable`, `disable`, `pause`, or `resume`. Removal calls `disable_drain_remove`, whose frozen core contract performs disable → await lock drain → cancel future jobs → delete credentials → write tombstone audit metadata. Credential edits require recent re-auth.

```python
@router.post("/accounts", status_code=201, dependencies=[Depends(require_csrf), Depends(require_recent_reauth)])
async def add_account(payload: AddAccountRequest, request: Request, accounts: AccountService = Depends(get_accounts)) -> ApiResponse[AccountView]:
    actor = Actor("webui", str(request.state.session.admin_id))
    if payload.auth_mode == "credentials":
        account = await accounts.add_credentials(payload.label, payload.game_username, payload.game_password.get_secret_value(), actor=actor)
    else:
        account = await accounts.add_token_only(payload.label, payload.session_token.get_secret_value(), actor=actor)
    return ApiResponse(data=AccountView.from_snapshot(await accounts.snapshot(account.id, actor=actor)), correlation_id=request.state.correlation_id)

@router.delete("/accounts/{account_id}", status_code=202, dependencies=[Depends(require_csrf), Depends(require_recent_reauth)])
async def remove_account(account_id: UUID, request: Request, accounts: AccountService = Depends(get_accounts)) -> ApiResponse[RemovalReceipt]:
    return ApiResponse(data=await accounts.disable_drain_remove(account_id, actor=Actor("webui", str(request.state.session.admin_id))), correlation_id=request.state.correlation_id)
```

Token creation accepts name, expiry, scopes, and either all accounts or an explicit allowlist, validates `game:read/game:operate/automation:manage/inventory:confirm` (the reserved `admin` class has no MCP tools in this release), stores only prefix/digest/metadata, and returns the full high-entropy secret in one response. Routes call `McpTokenService.list_metadata`, `get_metadata`, `create`, `rotate`, and `revoke` without accessing its store directly. Rotation creates a new token before revoking the old one; revoke is immediate. The UI warns when scopes permit batch or confirmation-required operations and copies the one-time secret only after an explicit reveal action.

```tsx
function OneTimeToken({ token, onDismiss }: { token: string; onDismiss(): void }) {
  const [revealed, setRevealed] = useState(false);
  return <section aria-labelledby="token-once"><h2 id="token-once">令牌仅显示一次</h2>
    {revealed ? <CopyableSecret value={token} /> : <button type="button" onClick={() => setRevealed(true)}>显示令牌</button>}
    <button type="button" onClick={onDismiss}>我已安全保存</button>
  </section>;
}
```

- [ ] **Step 4: Run API and component tests**

Run: `uv run pytest tests/admin/test_api_contract.py -q && npm --prefix web test -- --run src/pages/AccountsPage.test.tsx`

Expected: account mode validation, sanitized responses, disable/remove ordering, token one-time reveal, scope warnings, and revocation tests pass.

- [ ] **Step 5: Commit account/token administration**

```bash
git add src/placegame/admin/schemas.py src/placegame/admin/routes/accounts.py src/placegame/admin/routes/tokens.py src/placegame/admin/app.py web/src/pages/AccountsPage.tsx web/src/pages/McpTokensPage.tsx web/src/components/ConfirmDialog.tsx tests/admin/test_api_contract.py web/src/pages/AccountsPage.test.tsx
git commit -m "feat: manage game accounts and scoped mcp tokens"
```

### Task 5: Add Dashboard, Actions, Inventory, Jobs, Audit, and Policy APIs

**Files:**
- Modify: `src/placegame/admin/schemas.py`
- Create: `src/placegame/admin/routes/actions.py`
- Create: `src/placegame/admin/routes/inventory.py`
- Create: `src/placegame/admin/routes/jobs.py`
- Create: `src/placegame/admin/routes/audit.py`
- Create: `src/placegame/admin/routes/settings.py`
- Modify: `src/placegame/admin/routes/accounts.py`
- Modify: `src/placegame/admin/app.py`
- Test: `tests/admin/test_api_contract.py`

**Interfaces:**
- Produces typed routes for dashboard snapshots, account tabs, low-risk plans, confirmation execution, policy diffs, jobs/audit filters, global pause, and stable per-account partial results. `AdminActionService.plan(account_id, action, payload, *, actor) -> ActionPlanView` and `execute(account_id, action, plan_id, confirm, *, actor) -> VerifiedActionResult` are the only route-level action dispatch methods; `PolicyService` uses the frozen core signatures.

- [ ] **Step 1: Write failing plan/policy/error contract tests**

```python
async def test_low_risk_action_is_plan_then_verified_result(admin_client):
    plan = await admin_client.post(f"/api/admin/v1/accounts/{A_ID}/plans/idle-collect", headers=mutation_headers("p1"), json={})
    assert plan.json()["data"]["expires_at"]
    result = await admin_client.post(f"/api/admin/v1/accounts/{A_ID}/actions/idle-collect", headers=mutation_headers("x1"), json={"plan_id": plan.json()["data"]["plan_id"]})
    assert result.json()["data"]["verified"] is True

async def test_policy_save_returns_diff_and_rejects_quality_above_blue(admin_client):
    response = await admin_client.put(f"/api/admin/v1/accounts/{A_ID}/automation/policy", headers=mutation_headers("policy-1"), json={"inventory_auto_quality_ceiling": "purple"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
```

- [ ] **Step 2: Run tests and verify missing route failure**

Run: `uv run pytest tests/admin/test_api_contract.py -q`

Expected: FAIL because plan/action, inventory, jobs, audit, and settings routers are not mounted.

- [ ] **Step 3: Implement route modules through shared services**

Read-only routes return refreshed authoritative snapshots: dashboard cards include label/character/job/level/power/map, auth/paused state, idle time, bag/warehouse pressure, next work, last success, and alert. Account tabs expose overview, idle, personal/ordinary/world bosses with optimizer explanation/costs, immutable profession specialization, inventory plans/protection reasons, automation, and audit.

Low-risk routes create a plan then execute its ID under the core account lock. `AdminActionService` maps fixed action names to core plan/execute services and to `InventoryService.sort`, cleanup, warehouse, replacement, item-use, equipment-action, and recycle methods; it has no dynamic import or endpoint dispatch. Confirmation routes require `require_recent_reauth`, explicit `confirm=true`, and the inventory `inventory:confirm` scope-equivalent administrator permission; server state is revalidated and no optimistic result is emitted. Policy updates parse the strict `AccountPolicy`, enforce idle range 1 hour through server capacity minus 10 minutes, chance 50–98, reserves non-negative, warning/critical ordering, and blue-or-lower automatic quality, then return a before/after diff. Job/audit routes filter by account, actor, operation, status, correlation ID, cost, reasons, retry history, and verified outcome while redacting secrets. Batch responses retain per-account partial success.

```python
@router.put("/accounts/{account_id}/automation/policy", dependencies=[Depends(require_csrf)])
async def update_policy(account_id: UUID, payload: PolicyUpdate, request: Request, policies: PolicyService = Depends(get_policies)) -> ApiResponse[PolicyDiff]:
    current = await policies.get(account_id)
    capacity = await policies.server_idle_capacity(account_id)
    candidate = AccountPolicy.model_validate({**current.model_dump(exclude={"version"}), **payload.model_dump(exclude_unset=True)})
    if candidate.idle_threshold_minutes > capacity - 10:
        raise AdminError("validation_error", "idle threshold exceeds server capacity", 422, {"idle_threshold_minutes": f"maximum is {capacity - 10}"})
    saved = await policies.save(account_id, candidate, expected_version=current.version, actor=Actor("webui", str(request.state.session.admin_id)))
    return ApiResponse(data=PolicyDiff.between(current, saved), correlation_id=request.state.correlation_id)

@router.post("/accounts/{account_id}/actions/{action}", dependencies=[Depends(require_csrf)])
async def execute_action(account_id: UUID, action: AllowedAdminAction, payload: ExecutePlanRequest, request: Request, actions: AdminActionService = Depends(get_actions)) -> ApiResponse[VerifiedActionResult]:
    scopes = frozenset()
    if payload.confirm:
        await require_recent_reauth(request, request.state.session)
        scopes = frozenset({"inventory:confirm"})
    actor = Actor("webui", str(request.state.session.admin_id), scopes)
    result = await actions.execute(account_id, action, payload.plan_id, payload.confirm, actor=actor)
    return ApiResponse(data=result, correlation_id=request.state.correlation_id)
```

- [ ] **Step 4: Run backend route tests**

Run: `uv run pytest tests/admin/test_api_contract.py -q`

Expected: plan expiry, confirmation, policy validation/diff, filters, partial batch, IDOR, and redacted-error tests pass.

- [ ] **Step 5: Commit the admin-domain API checkpoint**

```bash
git add src/placegame/admin/schemas.py src/placegame/admin/routes src/placegame/admin/app.py tests/admin/test_api_contract.py
git commit -m "feat: expose policy action inventory and audit admin api"
```

### Task 6: Build Responsive Dashboard and Account Information Architecture

**Files:**
- Create: `web/src/router.tsx`
- Create: `web/src/auth/ProtectedRoute.tsx`
- Create: `web/src/components/AccountCard.tsx`
- Create: `web/src/components/StatusBadge.tsx`
- Create: `web/src/components/PlanPreview.tsx`
- Create: `web/src/components/ProtectionBadge.tsx`
- Create: `web/src/pages/DashboardPage.tsx`
- Create: `web/src/pages/AccountPage.tsx`
- Create: `web/src/pages/JobsPage.tsx`
- Create: `web/src/pages/AuditPage.tsx`
- Create: `web/src/styles.css`
- Test: `web/src/components/AccountCard.test.tsx`
- Test: `web/src/components/PlanPreview.test.tsx`

**Interfaces:**
- Produces responsive routes `/`, `/accounts`, `/accounts/:id/:tab`, `/jobs`, `/audit`, `/mcp-tokens`, and `/settings`, with accessible status and plan components.

- [ ] **Step 1: Write failing component/accessibility tests**

```tsx
it("shows account context and text plus icon for critical pressure", async () => {
  render(<AccountCard account={criticalAccount} />);
  expect(screen.getByRole("heading", { name: "开到荼蘼" })).toBeVisible();
  expect(screen.getByText("背包临界")).toBeVisible();
  expect(screen.getByLabelText("critical inventory pressure")).toBeVisible();
});

it("requires an explicit confirmation action for destructive plans", async () => {
  render(<PlanPreview plan={purpleDecomposePlan} onExecute={vi.fn()} />);
  expect(screen.getByText("需要确认")).toBeVisible();
  expect(screen.getByRole("button", { name: "确认并执行" })).toBeDisabled();
  await userEvent.click(screen.getByRole("checkbox", { name: "我已阅读不可逆影响" }));
  expect(screen.getByRole("button", { name: "确认并执行" })).toBeEnabled();
});
```

- [ ] **Step 2: Run component tests and verify they fail**

Run: `npm --prefix web test -- --run src/components/AccountCard.test.tsx src/components/PlanPreview.test.tsx`

Expected: FAIL because routes and components are not implemented.

- [ ] **Step 3: Implement responsive pages and semantic state cues**

Dashboard cards display label, character, job, level, power, map, authentication/paused state, idle countdown, bag/warehouse pressure, next work, last success, and alert, with filters for enabled/paused/warning/auth-required/critical. Bulk actions are only pause/resume/refresh/safe maintenance/run-now and show exact selected labels/count.

Account detail uses the seven tabs from the design. Boss views separate personal, ordinary solo, and world collaboration and render chosen difficulty, skills, tactic, affix, chance, boost, potion, costs, and rejected alternatives. Profession tab renders `selectedProfessionKey` with an immutable lock icon. Inventory tab uses `ProtectionBadge` text plus icon, quality/slot/score/lock/bind/protection filters, separate bag/warehouse tables, preview exclusions, and copyable IDs only in a detail disclosure. Familiar tool buttons use Lucide icons with accessible names and tooltips where the symbol is not self-evident. CSS switches to the compact layout at 720px, uses keyboard-visible focus rings, logical headings, `aria-live` for job updates, and no color-only state; both configured viewports must have no horizontal overflow, clipped controls, or overlapping text.

```tsx
export function AccountCard({ account }: { account: AccountCardModel }) {
  return <article className="account-card" aria-labelledby={`account-${account.id}`}>
    <h2 id={`account-${account.id}`}>{account.character ?? account.label}</h2>
    <p>{account.label} · {account.job} · Lv.{account.level} · {account.power}</p>
    <StatusBadge state={account.auth_state} />
    <StatusBadge state={account.inventory_pressure} label={account.inventory_pressure === "critical" ? "背包临界" : undefined} />
    <p>挂机：{account.idle_minutes} 分钟；下次任务：{account.next_work ?? "无"}</p>
    {account.alert ? <p role="alert">{account.alert.message}</p> : null}
  </article>;
}

export function PlanPreview({ plan, onExecute }: PlanPreviewProps) {
  const [ack, setAck] = useState(false);
  return <section aria-labelledby="plan-title"><h2 id="plan-title">执行预览</h2>
    <PlanCosts plan={plan} /><ProtectionBadges exclusions={plan.exclusions} />
    {plan.confirmation_required && <label><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} /> 我已阅读不可逆影响</label>}
    <button type="button" disabled={plan.confirmation_required && !ack} onClick={onExecute}>确认并执行</button>
  </section>;
}
```

The initial page load does not focus or submit a destructive confirmation button, so pressing Enter cannot trigger a mutation before the explicit checkbox action.

- [ ] **Step 4: Run component, type, and responsive checks**

Run: `npm --prefix web run typecheck && npm --prefix web test -- --run && npm --prefix web run build`

Expected: zero TypeScript errors, all component/accessibility tests pass, and a production bundle is emitted.

- [ ] **Step 5: Commit the responsive UI checkpoint**

```bash
git add web/src/router.tsx web/src/auth/ProtectedRoute.tsx web/src/components web/src/pages web/src/styles.css web/src/components/*.test.tsx
git commit -m "feat: add responsive account dashboard and detail views"
```

### Task 7: Add SSE Live Updates, Policy Editor, and Failure-Recovery UX

**Files:**
- Modify: `src/placegame/admin/auth.py`
- Modify: `src/placegame/admin/sessions.py`
- Modify: `src/placegame/admin/schemas.py`
- Modify: `src/placegame/admin/routes/settings.py`
- Modify: `src/placegame/admin/app.py`
- Create: `src/placegame/admin/routes/events.py`
- Create: `src/placegame/admin/events.py`
- Create: `web/src/components/EventStream.tsx`
- Create: `web/src/pages/SettingsPage.tsx`
- Modify: `web/src/pages/AccountPage.tsx`
- Modify: `web/src/api/query.ts`
- Test: `tests/admin/test_events.py`
- Test: `tests/admin/test_security.py`
- Test: `web/src/components/EventStream.test.tsx`
- Test: `web/src/pages/SettingsPage.test.tsx`

**Interfaces:**
- Produces `GET /api/admin/v1/events` SSE with monotonic IDs and `Last-Event-ID` replay, `AdminEventBus.publish(kind, public_payload) -> AdminEvent` and `subscribe(after_id) -> AsyncIterator[AdminEvent]`, typed policy/settings APIs, `AdminAuthService.change_password/rotate_totp/regenerate_recovery_codes`, `SessionService.revoke_other_admin_sessions`, and a 30-second polling fallback.

- [ ] **Step 1: Write failing SSE/policy-form tests**

```python
async def test_events_resume_after_last_event_id(admin_client, event_bus):
    await event_bus.publish("account.updated", {"account_id": str(A_ID), "password": "never"})
    await event_bus.publish("job.updated", {"account_id": str(A_ID), "status": "succeeded"})
    events = await admin_client.sse("/api/admin/v1/events", headers={"Last-Event-ID": "1"})
    assert [event.id for event in events] == ["2"]
    assert "password" not in events[0].data

async def test_security_rotation_requires_recent_reauth_and_revokes_other_sessions(admin_client, second_admin_session):
    denied = await admin_client.post("/api/admin/v1/settings/recovery-codes/regenerate", headers=mutation_headers("codes-1"), json={})
    assert denied.status_code == 401 and denied.json()["error"]["code"] == "reauth_required"
    await reauthenticate_with_password_and_totp(admin_client)
    result = await admin_client.post("/api/admin/v1/settings/sessions/revoke", headers=mutation_headers("sessions-1"), json={"except_current": True})
    assert result.status_code == 200
    assert (await second_admin_session.get("/api/admin/v1/auth/me")).status_code == 401
```

```tsx
it("rejects impossible policy combinations and presents a diff", async () => {
  render(<PolicyEditor policy={defaultPolicy} />);
  await userEvent.selectOptions(screen.getByLabelText("自动分解品质上限"), "purple");
  expect(screen.getByText("自动上限不能高于蓝色")).toBeVisible();
  expect(screen.getByRole("button", { name: "保存策略" })).toBeDisabled();
});
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/admin/test_events.py tests/admin/test_security.py -q && npm --prefix web test -- --run src/components/EventStream.test.tsx src/pages/SettingsPage.test.tsx`

Expected: FAIL because the event bus, SSE route, policy editor, and security-setting services are missing.

- [ ] **Step 3: Implement sanitized replayable events and typed policy controls**

Store a PostgreSQL `admin_events` sequence with a monotonic `BIGSERIAL` ID and bounded retention, publish sanitized account snapshots, job transitions, and alert summaries, and replay IDs greater than `Last-Event-ID`. Sensitive mutation outcomes are omitted from broadcasts and fetched from the authenticated detail route. On disconnect the React `EventStream` reconnects with the last ID; after two failures it starts a 30-second polling query and announces stale status.

```python
@router.get("/events")
async def events(request: Request, session: AdminSession = Depends(require_admin), bus: AdminEventBus = Depends(get_event_bus)) -> StreamingResponse:
    last_id = int(request.headers.get("Last-Event-ID", "0"))
    async def stream():
        async for event in bus.subscribe(after_id=last_id):
            if await request.is_disconnected():
                break
            safe = redact(event.public_payload)
            yield f"id: {event.id}\nevent: {event.kind}\ndata: {json.dumps(safe, ensure_ascii=False)}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
```

```tsx
export function EventStream() {
  const queryClient = useQueryClient();
  useEffect(() => {
    let failures = 0;
    const source = new EventSource("/api/admin/v1/events", { withCredentials: true });
    source.onopen = () => { failures = 0; disablePollingFallback(); };
    source.addEventListener("account.updated", event => queryClient.setQueryData(["account", JSON.parse((event as MessageEvent).data).account_id], JSON.parse((event as MessageEvent).data)));
    source.addEventListener("job.updated", () => queryClient.invalidateQueries({ queryKey: ["jobs"] }));
    source.onerror = () => { if (++failures >= 2) enablePollingFallback(30_000); };
    return () => source.close();
  }, [queryClient]);
  return <span className="sr-only" aria-live="polite">实时更新已连接</span>;
}
```

The policy editor exposes idle threshold (60 minutes through server capacity minus 10, default 690), boss chance (50–98, default 80), paid-attempt toggle (off), world collaboration (enabled, exactly three attempts per eligible boss), material reserves (non-negative, default 64), profession targets/horizon, inventory thresholds (85%/95%), automatic quality ceiling (`white|green|blue`, default blue), and safe-reward toggle (on) as typed controls. It displays Beijing `UTC+8`, before/after diff, owned/cost/reserve/projected remainder values, and a global-pause banner distinguishing manual, schema-protection, and update-required states. Global settings also expose scheduler lease health, global pause, defaults inherited by new accounts, concurrency (default four), audit retention, password/TOTP/recovery regeneration, and administrator-session revocation. Password change requires the current password plus TOTP; TOTP rotation returns a new encrypted server-generated secret/QR and activates it only after a valid new code; recovery regeneration invalidates every old code and returns ten replacements once; session revocation preserves only the current session when requested. All security settings require recent password-plus-TOTP re-authentication and write a sanitized audit event. Critical inventory pressure and missed world-boss alerts remain until acknowledged.

Authentication-required account cards link to a dedicated credential/token recovery action. A server conflict removes the stale plan from the cache and offers regeneration; a network interruption labels mutation state “待核验” until the detail endpoint returns authoritative state. Batch views render success/failure per account, never replace a partial result with one global success banner.

- [ ] **Step 4: Run live-update and policy tests**

Run: `uv run pytest tests/admin/test_events.py tests/admin/test_security.py -q && npm --prefix web test -- --run src/components/EventStream.test.tsx src/pages/SettingsPage.test.tsx && npm --prefix web run typecheck`

Expected: SSE replay/redaction, reconnect/poll fallback, field validation, diff, alert persistence, and type tests pass.

- [ ] **Step 5: Commit live updates and settings**

```bash
git add src/placegame/admin/auth.py src/placegame/admin/sessions.py src/placegame/admin/schemas.py src/placegame/admin/routes/settings.py src/placegame/admin/routes/events.py src/placegame/admin/events.py src/placegame/admin/app.py web/src/components/EventStream.tsx web/src/pages/SettingsPage.tsx web/src/pages/AccountPage.tsx web/src/api/query.ts tests/admin/test_events.py tests/admin/test_security.py web/src/components/EventStream.test.tsx web/src/pages/SettingsPage.test.tsx
git commit -m "feat: add live admin events and typed policy editor"
```

### Task 8: Serve Built Assets and Run End-to-End Acceptance

**Files:**
- Modify: `.env.example`
- Modify: `src/placegame/app.py`
- Create: `web/e2e/admin-flow.spec.ts`
- Create: `tests/admin/e2e_server.py`
- Create: `tests/admin/test_acceptance.py`

**Interfaces:**
- Produces built frontend assets, application security headers, SPA fallback, and a passing management-flow/security acceptance suite consumed by the deployment plan.

- [ ] **Step 1: Write failing application-boundary and end-to-end checks**

```python
def test_application_security_headers(admin_client):
    response = admin_client.get("/")
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"

async def test_full_admin_api_flow(admin_api, fake_game):
    await admin_api.complete_setup_and_login()
    account = await admin_api.add_token_only_account("A", fake_game.session_token)
    plan = await admin_api.preview_safe_cleanup(account.id)
    assert (await admin_api.execute_plan(account.id, plan.plan_id)).verified
    token = await admin_api.create_mcp_token(scopes=["game:read"], account_ids=[account.id])
    await admin_api.revoke_mcp_token(token.id)
    assert not admin_api.captured_output_contains_secret(token.secret)
```

```typescript
// web/e2e/admin-flow.spec.ts
test("administrator can add an account, execute a previewed cleanup, and revoke a token", async ({ page }) => {
  await completeSetup(page);
  await loginWithTotp(page);
  await addTokenOnlyAccount(page, "A", "e2e-session-token");
  await page.getByRole("link", { name: "A" }).click();
  await page.getByRole("tab", { name: "Inventory" }).click();
  await page.getByRole("button", { name: "预览安全清理" }).click();
  await page.getByRole("button", { name: "确认并执行" }).click();
  await createAndRevokeReadToken(page);
  await expect(page.getByText(/Bearer |e2e-session-token/)).toHaveCount(0);
});
```

- [ ] **Step 2: Run checks and verify they fail before static serving is wired**

Run: `uv run pytest tests/admin/test_acceptance.py -q; npm --prefix web run test:e2e`

Expected: FAIL until built assets, security headers, and SPA fallback are configured.

- [ ] **Step 3: Implement static serving and application security policy**

`npm run build` emits `web/dist`; `create_app` mounts the hashed assets and returns `index.html` only for unknown non-API, non-MCP GET paths. API errors remain JSON and unknown `/api` or `/mcp` paths never fall through to the SPA. Application middleware sets `Content-Security-Policy: default-src 'self'; connect-src 'self'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and `X-Frame-Options: DENY`. The Task 3 middleware retains separate login/MCP/read/mutation rate buckets. `/mcp` keeps bearer authentication and explicitly ignores the session cookie; admin dependencies reject MCP bearer tokens. TLS, redirect, HSTS, compression, loopback publication, and the production image belong to the deployment plan and later operator-managed 1Panel configuration.

Register startup/shutdown hooks for DB, scheduler, and event bus, and expose `/api/admin/v1/auth/status` before setup. The acceptance fixture starts the fake game server and app with ten isolated accounts, runs idle/boss/inventory plans, verifies job history, proves WebUI/MCP/scheduler produce the same decision for an identical snapshot and policy, checks partial batch results and one-account token isolation, and verifies no secret appears in HTML, JSON, SSE, logs, or audit responses. Playwright also asserts no horizontal overflow or overlapping primary controls at both configured viewports.

`tests/admin/e2e_server.py` refuses non-loopback binding, starts an ephemeral PostgreSQL testcontainer, injects the in-process fake game transport, serves the built `web/dist` and API on `127.0.0.1:4173`, and stops only that testcontainer on shutdown. It never reads production environment credentials.

Set `.env.example` to `PLACEGAME_PUBLIC_BASE_URL=` so the initial deployment has no implied domain. The application does not emit HSTS on plain loopback HTTP; the later 1Panel virtual host owns HSTS after certificate and domain validation.

- [ ] **Step 4: Run the complete verification suite**

Run: `uv run pytest -q && npm --prefix web ci && npm --prefix web run typecheck && npm --prefix web test -- --run && npm --prefix web run build && npm --prefix web exec -- playwright install chromium && npm --prefix web run test:e2e`

Expected: backend, frontend, accessibility, E2E, and application-security tests pass with no secret output.

- [ ] **Step 5: Commit the WebUI acceptance release**

```bash
git add .env.example src/placegame/app.py web/e2e tests/admin/e2e_server.py tests/admin/test_acceptance.py
git commit -m "feat: complete webui acceptance surface"
```

## WebUI Self-Review Checklist

- Spec coverage: Tasks 1–3 cover typed API envelopes, one-time setup, Argon2id/TOTP/recovery, lockout, server sessions, cookie flags, CSRF, re-auth, idempotency, and scope separation; Task 4 covers both account credential modes, removal/tombstones, and one-time MCP token reveal/revocation; Task 5 covers every dashboard/detail tab, plan/confirmation flow, policy validation/diff, jobs/audit/settings, and partial batch results; Tasks 6–7 cover responsive/accessibility rules, protection badges, optimizer explanations, SSE resume/poll fallback, alerts, and failure UX; Task 8 covers static serving, application security headers, and acceptance criteria. Production image and edge boundaries are covered by the deployment plan.
- Placeholder scan command: `rg -n -i "T[O]DO|T[B]D|F[I]XME|implement[ ]later|fill[ ]in|write[ ]tests[ ]for[ ]the[ ]above|appropriate[ ]error[ ]handling|similar[ ]to[ ]task" docs/superpowers/plans/2026-08-17-placegame-webui.md`; expected output is empty.
- Type/signature check: `uv run pyright src/placegame/admin tests/admin` and `npm --prefix web run typecheck` must report zero errors; route paths, envelopes, session dependencies, and inventory/core service calls must match the cross-plan contracts above.
- Fresh verification: `uv run pytest -q`, `npm --prefix web ci`, `npm --prefix web test -- --run`, `npm --prefix web run build`, `npm --prefix web exec -- playwright install chromium`, and `npm --prefix web run test:e2e` must succeed before moving to deployment tasks.
