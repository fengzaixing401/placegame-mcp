# PlaceGame P2 Streamable HTTP MCP Design

**Date:** 2026-08-19

**Status:** Approved design direction

**Depends on:** P1 shared multi-account idle backend at `f2494ef`

## 1. Goal

P2 exposes the approved P1 account-status and idle-preview use cases to remote
agents through Streamable HTTP MCP. It remains a server-hosted,
single-operator, multi-game-account service and runs MCP in the existing
FastAPI/Uvicorn container.

The production MCP surface is read and plan only. Live idle collection remains
unavailable because `docs/contracts/placegame-idle-contract-status.md` is
still `live_contract_unverified`.

## 2. Decision and Alternatives

### 2.1 Recommended: same-process FastMCP with one static token

Mount a FastMCP 1.29 Streamable HTTP application inside the existing FastAPI
composition root. Protect the MCP subapplication with one static Bearer token
loaded from a runtime secret. Tools are thin adapters over the existing P1 use
cases.

This is the smallest design that provides a real remote MCP endpoint while
retaining one database, one HTTP client, one account-locking implementation,
and one lifecycle owner. It matches the product's single-operator boundary and
does not invent token administration before there is a product need.

### 2.2 Rejected: revive database-backed scoped MCP tokens

The historical core design proposed database tokens, scopes, account
allowlists, rotation, and token-management APIs. That design serves multiple
operators or delegated access, neither of which exists in the approved product
boundary. Connecting the dormant `mcp_tokens` table would add hashing,
issuance, revocation, scope resolution, database lookups on authentication, and
an administration workflow with no current consumer.

P2 does not use or delete the dormant table. It remains schema-compatible
historical state and can be reconsidered only through a later design that
changes the single-operator requirement.

### 2.3 Rejected: MCP sidecar

A sidecar would require an internal API or duplicated application wiring,
separate health and lifecycle handling, another image/process, and a second
security boundary. It would not improve isolation because tools must still use
the same P1 locks, plans, database, and game HTTP client. The modular-monolith
boundary is sufficient and avoids this coordination cost.

FastMCP's OAuth/resource-server mode is also out of scope. A static token is
the deliberate authentication mechanism for P2, so OAuth metadata and dynamic
client registration are not exposed.

## 3. Scope and Overrides

P2 supersedes the MCP adapter and MCP surface sections of
`2026-08-17-placegame-mcp-core-design.md` for this milestone. In particular,
P2 has no scoped tokens, account allowlists, batch selectors, token management,
resources, prompts, scheduler tools, WebUI tools, or broad game-operation tool
catalog.

P2 adds:

- one Streamable HTTP MCP endpoint at `/mcp`;
- one static Bearer authentication boundary;
- three production tools and one test-only execution tool;
- safe adapter-level error mapping;
- protocol, security, lifecycle, and image smoke coverage.

P2 does not add:

- MCP account credential creation or editing;
- token creation, rotation, revocation, scopes, RBAC, or allowlists;
- OAuth authorization-server or protected-resource metadata;
- batch account selectors or partial batch results;
- resources, prompts, sampling, elicitation, or server-initiated workflows;
- scheduler, WebUI, administrator sessions, or multi-user behavior;
- a sidecar, Redis, queue, or new persistence model;
- GHCR login, image push, deployment, domain, TLS, or reverse-proxy changes.

## 4. Runtime Architecture

The process remains a single Uvicorn application:

```text
Remote agent
    |
    | POST/GET/DELETE /mcp + static Bearer token
    v
FastAPI parent application
    |-- /health/live
    |-- /health/ready
    `-- root fallback mount
          `-- StaticBearerAuthMiddleware
                `-- FastMCP Streamable HTTP route /mcp
                      `-- P2 adapter
                            |-- AccountStatusQuery
                            |-- IdlePlanUseCase
                            `-- IdleExecuteUseCase (test mode only)
```

FastMCP is constructed with the installed 1.29 API:

```python
FastMCP(
    "PlaceGame MCP",
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    max_request_body_size=65_536,
)
```

The implementation raises the runtime dependency floor to `mcp>=1.29,<2` so
these constructor and session-manager APIs are guaranteed by the declared
environment.

### 4.1 Exact mount semantics

The FastMCP child retains its route at `/mcp`. The parent declares both health
routes first, then mounts the authenticated MCP child as the root fallback
with `app.mount("/", authenticated_mcp_app, name="mcp")`. Starlette removes no
additional prefix from the child in this arrangement, so the external MCP URL
is exactly `/mcp`, not `/mcp/` and not `/mcp/mcp`.

The root mount is a routing mechanism, not a broad MCP surface. The child has
one route, `/mcp`; after authentication, `/mcp/` redirects to that canonical
path and any other fallback path receives 404. Health routes match in the
parent before the fallback and remain public. FastAPI documentation and OpenAPI
routes remain disabled.

This arrangement is intentional. Mounting a child configured with `/` under
`/mcp` makes Starlette redirect `/mcp` to `/mcp/` before child middleware can
authenticate it, which would violate the uniform unauthorized response.

### 4.2 Transport security

FastMCP's DNS-rebinding protection remains enabled. P2 adds a validated
`PLACEGAME_MCP_ALLOWED_HOSTS` setting represented as a JSON array of exact
hosts or SDK-supported `host:*` patterns. Its default contains only
`127.0.0.1:*`, `localhost:*`, and `[::1]:*`, which covers local and CI smoke
tests. A later operator-managed public deployment must set the exact public
Host value; P2 does not change the reverse proxy.

No browser client is part of P2, so `allowed_origins` remains empty. Requests
without an Origin are accepted by FastMCP, while browser-originated requests
are rejected. DNS-rebinding protection is never disabled as a shortcut.

## 5. Module Boundaries

P2 introduces a small `placegame.mcp` package:

- `src/placegame/mcp/auth.py` owns token syntax validation, constant-time
  comparison, and the pure ASGI Bearer middleware. It has no database or MCP
  protocol dependency.
- `src/placegame/mcp/adapter.py` constructs FastMCP, registers tools, creates
  the fixed MCP actor and server correlation IDs, and maps exceptions to safe
  `ToolError` codes. It depends on P1 use-case interfaces, not repositories or
  the game client.
- `src/placegame/app.py` remains the composition root. It creates the adapter,
  obtains `streamable_http_app()`, wraps it with authentication, mounts it, and
  owns the combined lifespan.
- `src/placegame/config.py` owns MCP token and allowed-host configuration and
  fail-closed startup validation.

The adapter contains no idle thresholds, plan construction, account locking,
claiming, reconciliation, or game endpoint knowledge. Those decisions remain
in `AccountStatusQuery`, `IdlePlanUseCase`, and `IdleExecuteUseCase`.

## 6. Lifespan and Resource Ownership

`FastMCP.streamable_http_app()` is called once during `create_app()` so its
lazy `session_manager` exists before startup. Mounted Starlette child
lifespans are not relied upon. The parent lifespan explicitly runs the MCP
manager:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with app.state.mcp_server.session_manager.run():
            yield
    finally:
        await app.state.http_client.aclose()
        await app.state.database.aclose()
```

The session manager starts once and stops before the shared HTTP client and
database are closed. The existing HTTP client and `Database` remain owned by
the parent and are closed exactly once. The child application does not close
them and its own lifespan is not entered separately.

## 7. Static Bearer Token

### 7.1 Configuration

`Settings` adds:

- `mcp_token: SecretStr | None`, alias `PLACEGAME_MCP_TOKEN`;
- `mcp_token_file: Path`, default
  `/run/secrets/placegame_mcp_token`;
- `mcp_allowed_hosts`, alias `PLACEGAME_MCP_ALLOWED_HOSTS`.

The environment token takes precedence. Otherwise startup reads the default
file as ASCII and strips one trailing line ending. The token must match
`[A-Za-z0-9_-]{43}`, the unpadded URL-safe representation produced by
`secrets.token_urlsafe(32)`. Missing, unreadable, blank, padded, whitespace-
containing, non-ASCII, too-short, or too-long values fail application startup
without logging the supplied value.

Production deployment uses the mounted file secret. `PLACEGAME_MCP_TOKEN` is
for tests and CI smoke only. Replacing the file and restarting the single
process is the only P2 rotation operation; there is no runtime rotation API.

### 7.2 ASGI authentication boundary

`StaticBearerAuthMiddleware` wraps the complete FastMCP child application. It
passes non-HTTP ASGI scopes unchanged and authenticates every HTTP method
before FastMCP parses a protocol message, allocates a session, or invokes a
tool.

For an HTTP request it:

1. reads the raw ASGI header list so duplicate fields are observable;
2. requires exactly one `Authorization` header;
3. requires ASCII and exactly `Bearer <token>` with one space;
4. compares the scheme case-insensitively;
5. validates the candidate token syntax;
6. compares the candidate and configured token with
   `secrets.compare_digest`;
7. forwards the request only after a match.

Every failure returns the same HTTP response:

```text
status: 401
WWW-Authenticate: Bearer
content-type: application/json
body: {"error":"unauthorized"}
```

The response never distinguishes missing, malformed, duplicate, or incorrect
credentials and never includes a token fragment. Unauthenticated oversized
bodies are rejected before being read. After authentication, FastMCP enforces
the 65,536-byte request-body limit.

## 8. MCP Tool Contracts

FastMCP registers tools with explicit names and structured output enabled.
Successful outputs are the existing strict, frozen P1 Pydantic DTOs (or a
sequence of `AccountSummary` DTOs). UUID arguments use their JSON UUID schema;
there are no URL, endpoint, raw payload, selector, credential, token, scope, or
correlation-ID arguments.

For every invocation the adapter creates:

```python
actor = Actor("mcp", "operator", frozenset())
correlation_id = uuid4().hex
```

The 32-character lowercase hexadecimal correlation ID satisfies P1's bounded
ASCII identifier contract. The adapter passes the actor and correlation ID to
every P1 operation that accepts them. `AccountStatusQuery.list()` is the
existing read-only P1 interface and has no actor parameter; the adapter does
not invent one.

### 8.1 `accounts_list`

```text
arguments: none
returns: sequence[AccountSummary]
delegates to: AccountStatusQuery.list()
```

Ordering remains the P1 repository order: label, then UUID. Listing never
claims `authenticated`; each result uses only `required` or `unknown`.

### 8.2 `account_status`

```text
arguments: account_id: UUID
returns: AccountStatus
delegates to: AccountStatusQuery.get(account_id, actor=actor)
```

This is an authoritative game read through P1. The returned model contains
sanitized account identity and idle state only.

### 8.3 `idle_preview`

```text
arguments: account_id: UUID
returns: IdlePreview
delegates to: IdlePlanUseCase.preview(
    account_id,
    actor=actor,
    correlation_id=correlation_id,
)
```

Preview may atomically persist one low-risk idle plan and its audit, but it
does not execute a mutation.

### 8.4 `idle_execute` test-only gate

```text
arguments: account_id: UUID, plan_id: UUID
returns: IdleExecution
delegates to: IdleExecuteUseCase.execute(
    account_id,
    plan_id,
    actor=actor,
    correlation_id=correlation_id,
)
```

The adapter registers this tool only when `Settings.test_mode is True`.
Registration is decided once while constructing FastMCP; production
`list_tools` therefore contains no mutation tool. Test mode already requires a
loopback game origin, so the tool exercises the real P1 claimed execution path
against the fake game server without reaching the live game.

There is no environment flag, request option, token variant, or alternate
route that enables `idle_execute` in production. The only future production
enablement path is a reviewed design after an opt-in live capture changes the
contract status from `live_contract_unverified`.

### 8.5 Advertised surface

Production `list_tools` returns exactly, in any SDK-defined ordering:

```text
accounts_list
account_status
idle_preview
```

Test mode returns exactly those three plus `idle_execute`. P2 registers no MCP
resources or prompts.

## 9. Error Mapping and Data Safety

The adapter catches expected exceptions around each use-case call and raises
`mcp.server.fastmcp.exceptions.ToolError` with one stable snake-case code. It
never returns `str(exception)`, `repr(exception)`, database messages, HTTP
status bodies, raw game responses, or validation state.

Mappings are:

| Exception | MCP tool error code |
| --- | --- |
| `ApplicationError` | its existing safe `.code` |
| `AccountNotFound` | `account_not_found` |
| `AccountIdentityConflict` | `account_identity_conflict` |
| `AccountDisabled` | `account_disabled` |
| `AccountPaused` | `account_paused` |
| `AccountRemoved` | `account_removed` |
| `AuthenticationRequired` | `authentication_required` |
| `PolicyUnavailable` | `policy_unavailable` |
| `ReconciliationRequired` | `reconciliation_required` |
| `PlanPreconditionFailed` | `plan_precondition_failed` |
| `SessionRejected` | `session_rejected` |
| `ContractChanged`, `GameSchemaMismatch` | `game_contract_changed` |
| `InventoryFull` | `inventory_full` |
| `InsufficientResource` | `insufficient_resource` |
| `GameConflict` | `game_conflict` |
| `GameRateLimited` | `game_rate_limited` |
| `AmbiguousMutation` | `ambiguous_mutation` |
| `GameUnavailable`, `GameHttpError` | `game_unavailable` |
| any other exception | `internal_error` |

FastMCP performs UUID argument validation before invocation. Those inputs are
identifiers, never secret-bearing fields. Unknown failures may be correlated
by the server-generated correlation ID, but adapter logs contain only the
stable code, tool name, account ID when validated, and correlation ID. They do
not contain exception text or arbitrary exception metadata.

Successful output is produced by Pydantic serialization of P1 DTOs. The
adapter does not convert database rows, game responses, or audit records
directly. Existing recursive audit redaction remains the persistence boundary.

## 10. Testing Strategy

### 10.1 Configuration and authentication tests

Focused unit tests prove:

- a valid environment token takes precedence over the file;
- a valid default file token loads as `SecretStr`;
- every missing or invalid token form fails startup without echoing the value;
- zero, duplicate, malformed, non-ASCII, wrong-scheme, and wrong-token
  Authorization headers all return the identical 401 response;
- scheme case differences are accepted;
- correct comparison uses the authenticated child for GET, POST, and DELETE;
- unauthorized requests never reach a sentinel child application;
- host allowlist validation retains FastMCP DNS-rebinding protection;
- the body limit is 65,536 bytes.

### 10.2 Adapter contract tests

Use fake P1 ports to verify tool names, argument schemas, strict DTO output,
the fixed MCP actor, unique server-generated correlation IDs, and every error
mapping. Inject exception messages containing credential, token, Authorization,
cookie, database, and raw-response markers and prove none appear in MCP output
or adapter logs.

Production settings must advertise exactly three tools. Test settings must
advertise exactly four and route `idle_execute` through the real P1 method
signature. Resource and prompt listings remain empty.

### 10.3 Protocol and PostgreSQL integration

Run the mounted ASGI application with a real temporary PostgreSQL database and
the existing account-aware typed fake game provider. The HTTP-only fake server
has path-static responses and therefore cannot prove per-token multi-account
isolation or the idle state transition around collection. Use MCP 1.29's real
client APIs:

```python
http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
async with streamable_http_client(url, http_client=http) as streams:
    async with ClientSession(streams[0], streams[1]) as session:
        await session.initialize()
        tools = await session.list_tools()
```

Tests prove initialize/list-tools over `/mcp`, account listing, authoritative
status, collect/wait preview, safe error codes, multi-account isolation, and
test-only execute through the real P1 claimed path. Production-mode protocol
tests prove that calling or listing `idle_execute` is impossible.

Lifecycle tests instrument the FastMCP session manager, shared HTTP client,
and `Database` to prove one start and one close each, including startup and
shutdown exceptions.

## 11. Docker and CI Gate

The Docker image keeps the existing entry point:

```text
uvicorn placegame.app:create_app --factory --host 0.0.0.0 --port 8000
```

No token is copied into the image. The GitHub Actions image smoke generates a
CI-only token with `secrets.token_urlsafe(32)` and passes it through
`PLACEGAME_MCP_TOKEN` together with the existing CI-only master key. Cleanup
remains unconditional.

The smoke gate must:

1. build the image;
2. start the container with generated CI-only secrets;
3. wait for `/health/live` to become healthy;
4. send an unauthenticated request to `/mcp` and require 401 plus
   `WWW-Authenticate: Bearer`;
5. create an `httpx.AsyncClient` carrying the generated Authorization header,
   run MCP `ClientSession.initialize()`, and call `list_tools()`;
6. require the production list to equal the three approved names;
7. remove the exact container even after failure.

The implementation gate also includes:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration" -q
.\.venv\Scripts\python.exe -m pytest -m integration -q
.\.venv\Scripts\python.exe -m pyright src/placegame
docker build -t placegame-mcp:p2 .
git diff --check
git status --short --branch
```

The canonical PostgreSQL integration gate runs on the isolated Singapore
runner with an explicit `PLACEGAME_TEST_DATABASE_URL` and must report zero
skips. The Docker smoke may run locally or on GitHub's `ubuntu-latest` runner,
but its exact result is recorded in the handoff. P2 does not log in to GHCR,
push an image, or deploy it; publication is a separate release milestone.

## 12. Acceptance Criteria

- The same FastAPI/Uvicorn process serves public health routes and MCP at the
  exact external path `/mcp`.
- FastMCP runs with `stateless_http=True`, `json_response=True`, a 65,536-byte
  request limit, explicit parent-owned session-manager lifespan, and enabled
  host validation.
- A missing or invalid configured MCP token prevents application construction;
  missing or invalid request authentication returns the uniform 401 boundary,
  without exposing secrets.
- Production advertises exactly `accounts_list`, `account_status`, and
  `idle_preview`; test mode alone additionally advertises `idle_execute`.
- Every tool delegates to an existing P1 use case and returns existing strict
  P1 DTOs. The adapter contains no game or policy decisions.
- Every actor-bearing call uses `Actor("mcp", "operator", frozenset())`; every
  preview or execution gets a fresh server-generated correlation ID.
- Known failures return only the documented stable error code. Unknown
  failures return only `internal_error`.
- No credential, game session token, MCP token, Authorization header, cookie,
  database exception, HTTP body, or raw game response appears in MCP results,
  HTTP authentication failures, logs, fixtures, or audit payloads.
- Production cannot discover or invoke idle collection while the live
  contract is unverified. There is no bypass flag.
- Shared HTTP, database, and MCP session-manager resources start and stop once.
- Protocol, PostgreSQL, Pyright, image, health, unauthorized, authorized
  initialize, and production tool-list gates pass with recorded exact output.
- P2 adds no MCP resources/prompts, WebUI, scheduler, token-management system,
  scoped authorization, deployment, or image publication.
