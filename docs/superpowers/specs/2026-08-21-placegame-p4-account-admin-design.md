# PlaceGame P4 Account Administration Design

## Goal

Complete the single-operator WebUI account-management vertical slice: create
credential and token-only game accounts, edit their label or same-mode
credentials, control lifecycle state, and remove an account through the
existing `AccountService`.

## Decisions

- Keep the existing versioned admin boundary: `/api/admin/v1`.
- Add no database migration. `GameAccount` and `AccountService` already hold
  both credential modes and lifecycle operations.
- Routes never access ORM records or secrets directly. Every operation uses
  `request.app.state.account_service` with `Actor("webui", "operator", frozenset())`.
- Secrets are write-only. Passwords and session tokens are accepted only in
  request bodies; they are never returned, logged, rendered into HTML, or
  included in error text.
- Keep authentication and same-origin/content-type checks from P3 for every
  state-changing route.
- Removal calls `disable_drain_remove`; the response is a sanitized account
  summary and the UI requires an explicit confirmation.

## API

All protected routes require the existing administrator cookie.

- `POST /api/admin/v1/accounts/credentials` body `{label, username, password}`;
  returns `201` and a sanitized account summary.
- `POST /api/admin/v1/accounts/token-only` body `{label, sessionToken}`;
  returns `201` and a sanitized account summary.
- `PATCH /api/admin/v1/accounts/{id}/label` body `{label}`; returns `200`.
- `PATCH /api/admin/v1/accounts/{id}/credentials` body
  `{username?, password}`; returns `200`.
- `PATCH /api/admin/v1/accounts/{id}/token-only` body `{sessionToken}`;
  returns `200`.
- `POST /api/admin/v1/accounts/{id}/enable`, `/disable`, `/pause`, and
  `/resume`; pause accepts `{reason}`; each returns `200`.
- `DELETE /api/admin/v1/accounts/{id}` calls `disable_drain_remove` and returns
  `200` with the final sanitized summary.

Successful summaries contain only account id, label, auth mode, enabled flag,
pause reason, session expiry, and timestamps. Existing stable error codes are
reused: `unauthorized`, `account_not_found`, `account_identity_conflict`,
`authentication_required`, `account_paused`, `account_removed`,
`account_disabled`, `invalid_request`, `internal_error`, and the existing
game/application mappings.

## UI

Extend the current static console with a create form, per-account edit form,
enable/disable/pause/resume controls, and a destructive remove confirmation.
After every mutation the form secret fields are cleared and the account list is
reloaded. Loading, validation, conflict, and network-error states remain
visible without exposing submitted values.

## Verification

The focused suite covers route authentication, service delegation and actor,
all request schemas, stable error mapping, lifecycle actions, removal, and
secret non-disclosure in response/HTML/error output. The existing full Python
suite and Pyright gate run once at handoff; no new browser framework is added.

## Explicitly out of scope

No authentication-mode conversion, batch import, pagination redesign, secret
export/rotation display, multi-user/RBAC, scheduler UI, audit UI, or new
background task system.
