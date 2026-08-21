# PlaceGame P4 Account Administration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real single-operator WebUI account-management slice on top of the existing `AccountService`.

**Architecture:** Extend the existing `/api/admin/v1` FastAPI router with typed write routes and sanitized summaries. The routes call `AccountService` only through `app.state`, using the fixed WebUI actor and existing error boundary. Extend the dependency-free static console to call these routes and refresh the list after each mutation.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy async services already in the repository, vanilla HTML/CSS/JavaScript, pytest, Pyright.

## Global Constraints

- Single administrator and multiple game accounts; no RBAC, multi-tenant behavior, or new migration.
- Passwords and session tokens are write-only and must not occur in responses, logs, HTML, or error details.
- All state-changing admin routes require the existing administrator cookie, same-origin check, and JSON content type.
- Reuse `AccountService`; do not duplicate credential verification, lifecycle rules, locking, or audit behavior in routes.
- Keep `/mcp` Bearer authentication independent and unchanged.
- Run focused tests while implementing and one full Python/Pyright gate at handoff.

---

### Task 1: Define sanitized account-management contracts and failing route tests

**Files:**
- Modify: `src/placegame/admin/routes.py`
- Test: `tests/unit/test_admin_routes.py`
- Create: `tests/unit/test_admin_account_management.py`

**Interfaces:**
- Request models: `CredentialsCreateRequest`, `TokenOnlyCreateRequest`,
  `LabelUpdateRequest`, `CredentialsUpdateRequest`,
  `TokenOnlyUpdateRequest`, and `PauseRequest`.
- Response helper: `managed_account_payload(account)` returns only non-secret
  fields from `ManagedAccount`.
- Routes call methods on `request.app.state.account_service` and pass
  `WEBUI_ACTOR`.

- [ ] **Step 1: Add failing contract tests**

  Build a fake `AccountService` with call recording and an in-memory admin
  session. Cover authenticated create credentials/token-only, label and
  same-mode credential updates, four lifecycle actions, delete/drain, and
  missing-cookie rejection. Assert every call receives
  `Actor("webui", "operator", frozenset())`.

- [ ] **Step 2: Run the focused tests and confirm the expected red**

  Run: `python -m pytest tests/unit/test_admin_account_management.py -q`
  Expected: collection or route-not-found failures because the new models and
  endpoints are absent.

- [ ] **Step 3: Implement typed models, summary serialization, and routes**

  Add strict, `extra="forbid"` Pydantic bodies with non-empty bounded fields.
  Register these endpoints under the existing router:

  ```text
  POST  /accounts/credentials
  POST  /accounts/token-only
  PATCH /accounts/{id}/label
  PATCH /accounts/{id}/credentials
  PATCH /accounts/{id}/token-only
  POST  /accounts/{id}/enable
  POST  /accounts/{id}/disable
  POST  /accounts/{id}/pause
  POST  /accounts/{id}/resume
  DELETE /accounts/{id}
  ```

  Use `invoke` and the existing `_ERROR_CODES` mapping. Return `201` for
  creation and `200` for successful updates/actions/removal. Never serialize a
  request model or exception into a response.

- [ ] **Step 4: Run focused route tests**

  Run: `python -m pytest tests/unit/test_admin_account_management.py tests/unit/test_admin_routes.py -q`
  Expected: all focused admin tests pass with no submitted secret in response
  bodies.

- [ ] **Step 5: Commit the backend slice**

  Run: `git add src/placegame/admin/routes.py tests/unit/test_admin_routes.py tests/unit/test_admin_account_management.py && git commit -m "feat: add web account management API"`

### Task 2: Extend the static console for account mutations

**Files:**
- Modify: `src/placegame/web/index.html`
- Modify: `src/placegame/web/app.js`
- Modify: `src/placegame/web/style.css`
- Test: `tests/unit/test_webui_static.py`

**Interfaces:**
- The existing same-origin `request` helper remains the only HTTP client.
- UI mutations accept the exact API bodies from Task 1 and always call
  `loadAccounts()` after completion.

- [ ] **Step 1: Add failing static contract assertions**

  Assert the HTML contains create/edit controls and a remove confirmation
  element, the JavaScript contains all mutation paths, and no literal secret
  value is embedded in the static bundle.

- [ ] **Step 2: Run the static tests and confirm red**

  Run: `python -m pytest tests/unit/test_webui_static.py -q`
  Expected: assertions fail because mutation controls and calls are absent.

- [ ] **Step 3: Implement the compact mutation UI**

  Add a create section with an auth-mode selector, label, username/password or
  session-token fields, and a submit button. Add per-row edit and lifecycle
  controls plus a confirmation prompt for removal. Disable the active control
  during a request, display stable server error codes, clear all secret fields
  in a `finally` block, and reload the list after success.

- [ ] **Step 4: Run static and route tests together**

  Run: `python -m pytest tests/unit/test_webui_static.py tests/unit/test_admin_account_management.py -q`
  Expected: all pass.

- [ ] **Step 5: Commit the WebUI slice**

  Run: `git add src/placegame/web tests/unit/test_webui_static.py && git commit -m "feat: add account controls to web console"`

### Task 3: Verification and handoff

**Files:**
- No planned production files; only fix files from Tasks 1-2 if a gate exposes a concrete issue.

- [ ] **Step 1: Run the focused acceptance gate**

  Run: `python -m pytest tests/unit/test_admin_account_management.py tests/unit/test_admin_routes.py tests/unit/test_webui_static.py tests/unit/test_app_bootstrap.py -q`

- [ ] **Step 2: Run type and syntax checks**

  Run: `python -m pyright src/placegame/admin src/placegame/web tests/unit/test_admin_account_management.py tests/unit/test_webui_static.py` and `python -m compileall -q src tests`.

- [ ] **Step 3: Run the full Python suite once**

  Run: `python -m pytest -q`.
  Expected: zero failures; existing integration skips remain explicit.

- [ ] **Step 4: Review the diff for secret leakage and route ordering**

  Check: `git diff --check`; search changed files for `password`,
  `sessionToken`, and `token` to ensure they occur only in request handling or
  field clearing, never in response serialization or visible status text.

- [ ] **Step 5: Handoff to Sol**

  Report the implementation commit, focused/full command output, and any
  unresolved deployment note. Sol performs one read-only review and returns
  `Approved` or a finite fix list.
