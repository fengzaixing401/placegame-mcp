# PlaceGame P2 Streamable HTTP MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the P1 multi-account status and idle-preview use cases through an authenticated `/mcp` Streamable HTTP endpoint, with idle execution registered only in loopback-only test mode.

**Architecture:** One FastMCP 1.29 server runs inside the existing FastAPI/Uvicorn process. A pure ASGI Bearer boundary authenticates before MCP parsing, the adapter delegates to P1 use cases, and the FastAPI parent owns the MCP session manager, shared HTTP client, and database lifetime.

**Tech Stack:** Python 3.12, FastAPI, Starlette ASGI, MCP Python SDK 1.29, Pydantic Settings 2, HTTPX, PostgreSQL/SQLAlchemy, pytest, Pyright, Docker, GitHub Actions.

**Implementation owner:** `gpt-5.6-terra`.

**Final review owner:** `gpt-5.6-sol` after the complete P2 milestone gate only.

## Global Constraints

- Work only in `D:\Ai\placegame-mcp\.worktrees\placegame-idle-v1` on branch `feat/placegame-idle-v1`.
- Do not modify, reset, clean, or commit the user-owned dirty Task 5C worktree at `D:\Ai\placegame-mcp\.worktrees\placegame-automation`.
- The product is server-hosted, single-operator, and multi-game-account; add no RBAC, 2FA, multi-user, or multi-tenant behavior.
- Declare `mcp>=1.29,<2`; configure FastMCP with child path `/mcp`, `stateless_http=True`, `json_response=True`, and `max_request_body_size=65_536`.
- Mount the authenticated child at the FastAPI root fallback so the exact external URL is `/mcp` with no redirect.
- Keep DNS-rebinding protection enabled. Default allowed hosts are exactly `127.0.0.1:*`, `localhost:*`, and `[::1]:*`; allowed origins are empty.
- Accept one 43-character unpadded URL-safe token from `PLACEGAME_MCP_TOKEN` or `/run/secrets/placegame_mcp_token`; the environment value wins.
- Production advertises exactly `accounts_list`, `account_status`, and `idle_preview`.
- Register `idle_execute` only when `Settings.test_mode is True`; production has no alternate enable flag, token, or route.
- `docs/contracts/placegame-idle-contract-status.md` remains `live_contract_unverified`; never expose a production idle mutation.
- Every actor-bearing call uses `Actor("mcp", "operator", frozenset())`; every invocation creates a fresh `uuid4().hex` correlation ID.
- Known errors return only their documented stable code; unknown errors return only `internal_error`.
- Never include credentials, sessions, MCP tokens, Authorization, cookies, exception text, database messages, HTTP bodies, or raw game responses in output, logs, fixtures, or audits.
- All mutation behavior remains in the existing P1 claimed path; never repeat an ambiguous game mutation.
- Add no MCP resources, prompts, batch selectors, scoped tokens, token administration, OAuth, scheduler, WebUI, sidecar, queue, deployment, or image publication.
- Modify only the files listed below. A need for another file returns to Sol planning before implementation proceeds.
- Follow red-green-refactor. Run focused tests during implementation, then the complete local and Singapore gates once at milestone handoff.

## File Responsibilities

- `src/placegame/mcp/__init__.py`: stable package exports.
- `src/placegame/mcp/auth.py`: token syntax and pure ASGI authentication only.
- `src/placegame/mcp/adapter.py`: tool registration, P1 delegation, actor/correlation creation, safe errors.
- `src/placegame/config.py`: token source and host configuration.
- `src/placegame/app.py`: composition, root fallback mount, combined lifespan.
- `tests/unit/test_mcp_auth.py`: configuration and raw-ASGI auth matrix.
- `tests/unit/test_mcp_adapter.py`: schemas, delegation, gate, and error matrix.
- `tests/unit/test_app_bootstrap.py`: routes and exactly-once lifecycle.
- `tests/integration/test_mcp_protocol.py`: real MCP client, PostgreSQL, account-aware typed fake.
- `pyproject.toml`, `uv.lock`: SDK floor and lock.
- `.github/workflows/build-image.yml`: Docker health, auth, initialize, tool-list smoke.

---

### Task 1: SDK Floor, Secret Configuration, and ASGI Authentication

**Files:**
- Create: `src/placegame/mcp/__init__.py`
- Create: `src/placegame/mcp/auth.py`
- Modify: `src/placegame/config.py`
- Create: `tests/unit/test_mcp_auth.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `validate_static_token(value: str) -> str`.
- Produces: `StaticBearerAuthMiddleware(app: ASGIApp, token: str)`.
- Produces: `Settings.read_mcp_token() -> SecretStr`.
- Produces: `Settings.mcp_allowed_hosts: list[str]`.

- [ ] **Step 1: Write failing token-source and raw-ASGI tests**

Create `tests/unit/test_mcp_auth.py`. Use `VALID_TOKEN = "A" * 43` and cover this exact matrix:

```python
@pytest.mark.parametrize("line_ending", ["", "\n", "\r\n"])
def test_file_token_accepts_one_optional_line_ending(tmp_path, line_ending):
    path = tmp_path / "mcp-token"
    path.write_bytes((VALID_TOKEN + line_ending).encode("ascii"))
    assert Settings(mcp_token_file=path).read_mcp_token().get_secret_value() == VALID_TOKEN


def test_environment_token_precedes_file(tmp_path):
    path = tmp_path / "mcp-token"
    path.write_text("B" * 43, encoding="ascii")
    settings = Settings(mcp_token=SecretStr(VALID_TOKEN), mcp_token_file=path)
    assert settings.read_mcp_token().get_secret_value() == VALID_TOKEN


@pytest.mark.parametrize(
    "value",
    ["", "A" * 42, "A" * 44, "A" * 42 + "=", "A" * 42 + "+", "é" * 43, VALID_TOKEN + "\n"],
)
def test_invalid_token_fails_without_echo(value):
    with pytest.raises(ValueError) as caught:
        Settings(mcp_token=SecretStr(value)).read_mcp_token()
    assert value not in str(caught.value)


def test_default_allowed_hosts_are_loopback_only():
    assert Settings().mcp_allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"]


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Basic " + VALID_TOKEN.encode())],
        [(b"authorization", b"Bearer")],
        [(b"authorization", b"Bearer wrong")],
        [(b"authorization", b"Bearer " + VALID_TOKEN.encode())] * 2,
        [(b"authorization", b"Bearer \xff")],
    ],
)
async def test_auth_failures_are_identical_and_do_not_read_body(headers):
    child = SentinelApp()

    async def forbidden_receive():
        raise AssertionError("unauthorized body was read")

    sent = await raw_asgi_request(
        StaticBearerAuthMiddleware(child, VALID_TOKEN),
        headers=headers,
        receive=forbidden_receive,
    )
    assert child.calls == []
    assert response_tuple(sent) == (
        401,
        b'{"error":"unauthorized"}',
        b"Bearer",
        b"application/json",
    )


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
async def test_valid_token_forwards_all_mcp_methods(method, scheme):
    child = SentinelApp()
    sent = await raw_asgi_request(
        StaticBearerAuthMiddleware(child, VALID_TOKEN),
        method=method,
        headers=[(b"authorization", f"{scheme} {VALID_TOKEN}".encode())],
    )
    assert child.calls == [method]
    assert sent[0]["status"] == 204
```

Implement `SentinelApp`, `raw_asgi_request`, and `response_tuple` in the same test file with raw ASGI scopes so duplicate and non-ASCII header bytes remain observable. Also assert a missing/unreadable/blank token file fails with the fixed message `MCP token secret is unavailable`, invalid host lists reject empty values, schemes, paths, unsupported wildcards, whitespace, and non-HTTP scopes pass through unchanged.

- [ ] **Step 2: Run the focused test and confirm red**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_mcp_auth.py -q
```

Expected: collection fails because the MCP package, settings, and middleware do not exist.

- [ ] **Step 3: Implement the minimal configuration and middleware**

Create `src/placegame/mcp/__init__.py` with the module docstring, and implement `src/placegame/mcp/auth.py`:

```python
import re
import secrets

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)


def validate_static_token(value: str) -> str:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise ValueError("MCP token must be a 43-character URL-safe secret")
    return value


class StaticBearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = validate_static_token(token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        values = [value for name, value in scope["headers"] if name.lower() == b"authorization"]
        candidate = None
        if len(values) == 1:
            try:
                scheme, candidate = values[0].decode("ascii").split(" ", 1)
                validate_static_token(candidate)
                if scheme.casefold() != "bearer":
                    candidate = None
            except (UnicodeDecodeError, ValueError):
                candidate = None
        if candidate is None or not secrets.compare_digest(candidate, self.token):
            await JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)
```

In `Settings`, add aliased `mcp_token`, `mcp_token_file`, and `mcp_allowed_hosts` fields. `read_mcp_token()` must read bytes as ASCII, remove at most one `\r\n` or `\n`, call `validate_static_token`, wrap the result in `SecretStr`, and convert `OSError`/`UnicodeDecodeError` to the fixed safe error. Add a `field_validator` that accepts exact ASCII hosts and SDK `host:*` patterns while rejecting the invalid-host test matrix.

Change `pyproject.toml` to `"mcp>=1.29,<2"`, then update only that locked package:

```powershell
uv lock --upgrade-package mcp
```

- [ ] **Step 4: Verify green and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_mcp_auth.py -q
uv lock --check
.\.venv\Scripts\python.exe -c "from importlib.metadata import version; print(version('mcp'))"
git add src/placegame/mcp/__init__.py src/placegame/mcp/auth.py src/placegame/config.py tests/unit/test_mcp_auth.py pyproject.toml uv.lock
git commit -m "feat: add authenticated MCP configuration"
```

Expected: all focused tests pass and MCP reports compatible version `1.29.0` or later 1.x.

### Task 2: FastMCP Tools, Test Gate, and Safe Error Boundary

**Files:**
- Create: `src/placegame/mcp/adapter.py`
- Modify: `src/placegame/mcp/__init__.py`
- Create: `tests/unit/test_mcp_adapter.py`

**Interfaces:**
- Produces protocols matching `AccountStatusQuery.list/get`, `IdlePlanUseCase.preview`, and `IdleExecuteUseCase.execute` exactly.
- Produces `MCP_ACTOR = Actor("mcp", "operator", frozenset())`.
- Produces `create_mcp_server(status_query, idle_plan, idle_execute_use_case, *, test_mode: bool, allowed_hosts: list[str]) -> FastMCP`.
- Produces `_invoke(operation: Callable[[], Awaitable[T]], *, tool_name: str, account_id: UUID | None, correlation_id: str) -> T` as the only adapter exception boundary.

- [ ] **Step 1: Write failing adapter tests with typed fakes**

Create `tests/unit/test_mcp_adapter.py` with fakes returning the existing `AccountSummary`, `AccountStatus`, `IdlePreview`, and `IdleExecution` models. Cover these exact assertions:

```python
async def test_production_surface_and_transport_settings():
    server, fakes = build_server(test_mode=False)
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == {"accounts_list", "account_status", "idle_preview"}
    assert tools["account_status"].inputSchema["properties"]["account_id"]["format"] == "uuid"
    assert all(tool.outputSchema is not None for tool in tools.values())
    assert server.settings.streamable_http_path == "/mcp"
    assert server.settings.stateless_http is True
    assert server.settings.json_response is True
    assert server.settings.max_request_body_size == 65_536
    assert server.settings.transport_security.enable_dns_rebinding_protection is True
    assert server.settings.transport_security.allowed_origins == []


async def test_delegation_uses_fixed_actor_and_fresh_correlations():
    server, fakes = build_server(test_mode=False)
    await server.call_tool("account_status", {"account_id": str(fakes.account_id)})
    await server.call_tool("idle_preview", {"account_id": str(fakes.account_id)})
    await server.call_tool("idle_preview", {"account_id": str(fakes.account_id)})
    assert fakes.status.actors == [MCP_ACTOR]
    assert all(call.actor == MCP_ACTOR for call in fakes.preview.calls)
    assert all(re.fullmatch(r"[0-9a-f]{32}", call.correlation_id) for call in fakes.preview.calls)
    assert fakes.preview.calls[0].correlation_id != fakes.preview.calls[1].correlation_id


async def test_test_mode_alone_registers_real_execute_signature():
    server, fakes = build_server(test_mode=True)
    plan_id = uuid4()
    assert {tool.name for tool in await server.list_tools()} == {
        "accounts_list", "account_status", "idle_preview", "idle_execute"
    }
    await server.call_tool("idle_execute", {"account_id": str(fakes.account_id), "plan_id": str(plan_id)})
    assert fakes.execute.calls[0].account_id == fakes.account_id
    assert fakes.execute.calls[0].plan_id == plan_id
    assert fakes.execute.calls[0].actor == MCP_ACTOR
```

Parameterize every design mapping: `ApplicationError.code`; all account errors; session, contract/schema, inventory/resource, conflict/rate limit, ambiguous, unavailable/HTTP errors. Assert direct `server.call_tool` raises `ToolError` ending in the stable code. Inject `Authorization: Bearer never-log`, cookie, database, and raw-body markers into known metadata and an unknown exception; assert only `internal_error` appears and `caplog` contains no marker, exception info, or traceback.

- [ ] **Step 2: Run the focused test and confirm red**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_mcp_adapter.py -q
```

Expected: collection fails because `placegame.mcp.adapter` does not exist.

- [ ] **Step 3: Implement the adapter**

Use P1-shaped protocols and construct FastMCP exactly as follows:

```python
server = FastMCP(
    "PlaceGame MCP",
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    max_request_body_size=65_536,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(allowed_hosts),
        allowed_origins=[],
    ),
)
```

Register structured-output closures with these exact bodies (`idle_execute_use_case` is the factory parameter name):

```python
@server.tool(name="accounts_list", structured_output=True)
async def accounts_list() -> tuple[AccountSummary, ...]:
    correlation_id = uuid4().hex
    return await _invoke(
        status_query.list,
        tool_name="accounts_list",
        account_id=None,
        correlation_id=correlation_id,
    )

@server.tool(name="account_status", structured_output=True)
async def account_status(account_id: UUID) -> AccountStatus:
    correlation_id = uuid4().hex
    return await _invoke(
        lambda: status_query.get(account_id, actor=MCP_ACTOR),
        tool_name="account_status",
        account_id=account_id,
        correlation_id=correlation_id,
    )

@server.tool(name="idle_preview", structured_output=True)
async def idle_preview(account_id: UUID) -> IdlePreview:
    correlation_id = uuid4().hex
    return await _invoke(
        lambda: idle_plan.preview(
            account_id, actor=MCP_ACTOR, correlation_id=correlation_id
        ),
        tool_name="idle_preview",
        account_id=account_id,
        correlation_id=correlation_id,
    )

if test_mode:
    @server.tool(name="idle_execute", structured_output=True)
    async def idle_execute(account_id: UUID, plan_id: UUID) -> IdleExecution:
        correlation_id = uuid4().hex
        return await _invoke(
            lambda: idle_execute_use_case.execute(
                account_id,
                plan_id,
                actor=MCP_ACTOR,
                correlation_id=correlation_id,
            ),
            tool_name="idle_execute",
            account_id=account_id,
            correlation_id=correlation_id,
        )
```

Each body creates one `uuid4().hex` and calls `_invoke` around the corresponding P1 method. `_invoke` catches `ApplicationError` first and uses `.code`; then map the named design exceptions; any unmatched `Exception` maps to `internal_error`. Log only the constant message `mcp_tool_failed` plus `tool_name`, validated account ID or `None`, correlation ID, and stable code through `extra`. Raise `ToolError(code) from None`; never log the exception object.

Export `create_mcp_server` and `StaticBearerAuthMiddleware` from `src/placegame/mcp/__init__.py`.

- [ ] **Step 4: Verify green and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_mcp_adapter.py -q
.\.venv\Scripts\python.exe -m pyright src/placegame/mcp
git add src/placegame/mcp/__init__.py src/placegame/mcp/adapter.py tests/unit/test_mcp_adapter.py
git commit -m "feat: expose safe MCP idle tools"
```

Expected: production has three tools, test mode four, DTO/schema assertions pass, all errors are stable, and Pyright reports zero errors for the package.

### Task 3: FastAPI Composition, Lifespan, and Real Protocol Integration

**Files:**
- Modify: `src/placegame/app.py`
- Modify: `tests/unit/test_app_bootstrap.py`
- Create: `tests/integration/test_mcp_protocol.py`

**Interfaces:**
- Consumes Task 1 auth/config and Task 2 `create_mcp_server`.
- Produces public parent health routes followed by `app.mount("/", authenticated_child, name="mcp")`.
- Produces `app.state.mcp_server`; calls `streamable_http_app()` exactly once.
- Produces parent lifespan ordering `mcp start -> mcp stop -> HTTP close -> database close`.

- [ ] **Step 1: Write failing composition and protocol tests**

Update `tests/unit/test_app_bootstrap.py` so every `create_app` call supplies `mcp_token=SecretStr("A" * 43)` and `mcp_allowed_hosts=["testserver"]`. Replace the health-only route assertion with:

```python
def test_app_has_health_then_one_root_mcp_fallback(settings):
    app = create_app(with_mcp(settings))
    assert [(route.path, route.name) for route in app.routes] == [
        ("/health/live", "live"),
        ("/health/ready", "ready"),
        ("", "mcp"),
    ]
    assert app.docs_url is app.redoc_url is app.openapi_url is None
```

Add unit tests proving unauthenticated `/mcp` returns the Task 1 401 without entering MCP lifespan, one normal parent lifespan produces exactly one start/stop/HTTP-close/database-close event in that order, and a forced `session_manager.run()` startup exception still closes HTTP and database once.

Create `tests/integration/test_mcp_protocol.py`. Run Alembic against `postgres_url`, truncate the same tables as `test_idle_application.py`, and reuse `ServiceEnvironment` plus its account-aware `FakeGameApiFactory`. Construct and mount the real P1 use cases with these helpers:

```python
def protocol_server(environment, *, test_mode: bool):
    repository = environment.service.repository
    return create_mcp_server(
        AccountStatusQuery(environment.service),
        IdlePlanUseCase(
            environment.service,
            IdlePreviewStore(environment.sessions, repository),
            clock=environment.clock,
        ),
        IdleExecuteUseCase(
            environment.service,
            IdleExecutionGuard(environment.sessions),
            IdleExecutionClaims(
                environment.sessions, repository, clock=environment.clock
            ),
        ),
        test_mode=test_mode,
        allowed_hosts=["testserver"],
    )


@asynccontextmanager
async def protocol_session(mcp_server):
    child = mcp_server.streamable_http_app()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/", StaticBearerAuthMiddleware(child, MCP_TOKEN))
    transport = httpx.ASGITransport(app=app)
    async with mcp_server.session_manager.run():
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
        ) as http:
            async with streamable_http_client(
                "http://testserver/mcp", http_client=http
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    yield session
```

Add these complete protocol cases:

```python
async def test_production_initialize_lists_three_tools_no_resources_or_prompts(mcp_environment):
    async with protocol_session(protocol_server(mcp_environment, test_mode=False)) as session:
        tools = await session.list_tools()
        resources = await session.list_resources()
        prompts = await session.list_prompts()
    assert {tool.name for tool in tools.tools} == {
        "accounts_list", "account_status", "idle_preview"
    }
    assert resources.resources == []
    assert prompts.prompts == []


async def test_two_accounts_list_status_and_collect_wait_previews_remain_isolated(mcp_environment):
    alpha, _ = await mcp_environment.add_token("alpha")
    beta, _ = await mcp_environment.add_token("beta")
    mcp_environment.fake.set_idle_seconds(alpha.id, 43_200)
    mcp_environment.fake.set_idle_seconds(beta.id, 1)
    async with protocol_session(protocol_server(mcp_environment, test_mode=False)) as session:
        listed = await session.call_tool("accounts_list")
        status = await session.call_tool("account_status", {"account_id": str(alpha.id)})
        alpha_preview = await session.call_tool("idle_preview", {"account_id": str(alpha.id)})
        beta_preview = await session.call_tool("idle_preview", {"account_id": str(beta.id)})
    assert [row["account_id"] for row in listed.structuredContent["result"]] == [
        str(alpha.id), str(beta.id)
    ]
    assert status.structuredContent["account"]["account_id"] == str(alpha.id)
    assert alpha_preview.structuredContent["decision"] == "collect"
    assert alpha_preview.structuredContent["plan_id"] is not None
    assert beta_preview.structuredContent["decision"] == "wait"
    assert beta_preview.structuredContent["plan_id"] is None


async def test_missing_account_returns_only_account_not_found(mcp_environment):
    missing = uuid4()
    async with protocol_session(protocol_server(mcp_environment, test_mode=False)) as session:
        result = await session.call_tool("account_status", {"account_id": str(missing)})
    rendered = " ".join(block.text for block in result.content if hasattr(block, "text"))
    assert result.isError is True
    assert "account_not_found" in rendered
    assert str(missing) not in rendered


async def test_test_mode_execute_uses_real_claimed_path_once(mcp_environment):
    account, _ = await mcp_environment.add_token("execute")
    mcp_environment.fake.set_idle_seconds(account.id, 43_200)
    async with protocol_session(protocol_server(mcp_environment, test_mode=True)) as session:
        preview = await session.call_tool("idle_preview", {"account_id": str(account.id)})
        executed = await session.call_tool(
            "idle_execute",
            {
                "account_id": str(account.id),
                "plan_id": preview.structuredContent["plan_id"],
            },
        )
    assert executed.isError is False
    assert executed.structuredContent["status"] == "executed"
    assert mcp_environment.fake.mutation_count("idle_collect", account.id) == 1


async def test_production_cannot_discover_or_call_idle_execute(mcp_environment):
    async with protocol_session(protocol_server(mcp_environment, test_mode=False)) as session:
        tools = await session.list_tools()
        result = await session.call_tool(
            "idle_execute",
            {"account_id": str(uuid4()), "plan_id": str(uuid4())},
        )
    assert "idle_execute" not in {tool.name for tool in tools.tools}
    assert result.isError is True
```

The isolation case adds two tokens, sets different idle seconds in the typed fake, and asserts account IDs and collect/wait previews do not cross. The test-mode case previews a collect plan, calls `idle_execute`, asserts `status == "executed"`, and asserts the fake mutation count is exactly one. The production case asserts exact discovery, empty resources/prompts, and an error when invoking the absent tool.

- [ ] **Step 2: Run the focused composition unit test and confirm red**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_app_bootstrap.py -q
```

Expected: route and lifecycle tests fail because the app has no MCP composition.

Do not run the PostgreSQL protocol file during this task. Its complete, zero-skip execution is part of Task 4's single Singapore milestone gate.

- [ ] **Step 3: Compose MCP and own its lifespan**

In `create_app`, read the token before allocating resources, build the server from the existing three P1 use cases, call its child-app factory once, and store the server:

```python
mcp_token = app.state.settings.read_mcp_token().get_secret_value()
app.state.mcp_server = create_mcp_server(
    app.state.account_status_query,
    app.state.idle_plan_use_case,
    app.state.idle_execute_use_case,
    test_mode=app.state.settings.test_mode,
    allowed_hosts=app.state.settings.mcp_allowed_hosts,
)
mcp_child = app.state.mcp_server.streamable_http_app()
```

Replace the parent lifespan and mount after both health routes:

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        async with _app.state.mcp_server.session_manager.run():
            yield
    finally:
        await _app.state.http_client.aclose()
        await _app.state.database.aclose()


app.mount("/", StaticBearerAuthMiddleware(mcp_child, mcp_token), name="mcp")
```

- [ ] **Step 4: Verify the local slice and commit**

Local:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_mcp_auth.py tests/unit/test_mcp_adapter.py tests/unit/test_app_bootstrap.py -q
```

Expected: focused unit tests pass. Do not repeat a remote protocol run here; Task 4 runs the full integration selection once with zero skips.

```powershell
git add src/placegame/app.py tests/unit/test_app_bootstrap.py tests/integration/test_mcp_protocol.py
git commit -m "feat: mount MCP with owned lifecycle"
```

### Task 4: Docker CI Smoke and Milestone Gate

**Files:**
- Modify: `.github/workflows/build-image.yml`

**Interfaces:**
- Keeps the existing image entry point unchanged.
- Produces generated CI-only secrets, health proof, exact unauthenticated response, authenticated initialize, production list-tools proof, and unconditional cleanup of container `placegame-p2`.
- Adds no registry login, push, or deployment.

- [ ] **Step 1: Observe the fail-closed image red state**

```powershell
docker build -t placegame-mcp:p2-red .
$masterKey = .\.venv\Scripts\python.exe -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
docker run --rm --name placegame-p2-red --env PLACEGAME_MASTER_KEY_B64=$masterKey placegame-mcp:p2-red
```

Expected: application construction fails safely because no MCP token was supplied. Remove only the named container if it remains.

- [ ] **Step 2: Extend the existing workflow with an exact MCP smoke**

Keep checkout and image build. Generate secrets without printing them and start the renamed container:

```bash
master_key="$(openssl rand -base64 32)"
mcp_token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker run --detach --name placegame-p2 \
  --env PLACEGAME_MASTER_KEY_B64="$master_key" \
  --env PLACEGAME_MCP_TOKEN="$mcp_token" \
  placegame-mcp:ci
```

Keep the existing 30-attempt Docker health loop with the new container name. Add `docker exec -i placegame-p2 python` with this smoke body:

```python
import asyncio
import os
import urllib.error
import urllib.request

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


request = urllib.request.Request("http://127.0.0.1:8000/mcp", method="GET")
try:
    urllib.request.urlopen(request, timeout=5)
except urllib.error.HTTPError as error:
    assert error.code == 401
    assert error.headers["WWW-Authenticate"] == "Bearer"
    assert error.read() == b'{"error":"unauthorized"}'
else:
    raise AssertionError("unauthenticated MCP request was accepted")


async def smoke() -> None:
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {os.environ['PLACEGAME_MCP_TOKEN']}"}
    ) as http:
        async with streamable_http_client(
            "http://127.0.0.1:8000/mcp", http_client=http
        ) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools = await session.list_tools()
    assert {tool.name for tool in tools.tools} == {
        "accounts_list", "account_status", "idle_preview"
    }


asyncio.run(smoke())
```

Retain an `if: always()` step whose exact cleanup command is:

```bash
docker rm --force placegame-p2 || true
```

- [ ] **Step 3: Validate the workflow and run the single milestone gate**

Local Windows:

```powershell
uv lock --check
.\.venv\Scripts\python.exe -m pytest -m "not integration" -q
.\.venv\Scripts\python.exe -m pyright src/placegame
.\.venv\Scripts\python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/build-image.yml', encoding='utf-8')); print('workflow yaml valid')"
git diff --check
git status --short --branch
```

Singapore with explicit `PLACEGAME_TEST_DATABASE_URL` and Docker runs the integration suite, then the exact image smoke:

```bash
./.venv/bin/python -m pytest -m integration -q
docker build -t placegame-mcp:p2 .
master_key="$(openssl rand -base64 32)"
mcp_token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker run --detach --name placegame-p2 \
  --env PLACEGAME_MASTER_KEY_B64="$master_key" \
  --env PLACEGAME_MCP_TOKEN="$mcp_token" \
  placegame-mcp:p2
for attempt in {1..30}; do
  status="$(docker inspect --format='{{.State.Health.Status}}' placegame-p2)"
  [ "$status" = "healthy" ] && break
  sleep 2
done
test "$(docker inspect --format='{{.State.Health.Status}}' placegame-p2)" = "healthy"
docker exec -i placegame-p2 python - <<'PY'
import asyncio
import os
import urllib.error
import urllib.request

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

request = urllib.request.Request("http://127.0.0.1:8000/mcp", method="GET")
try:
    urllib.request.urlopen(request, timeout=5)
except urllib.error.HTTPError as error:
    assert error.code == 401
    assert error.headers["WWW-Authenticate"] == "Bearer"
    assert error.read() == b'{"error":"unauthorized"}'
else:
    raise AssertionError("unauthenticated MCP request was accepted")

async def smoke():
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {os.environ['PLACEGAME_MCP_TOKEN']}"}
    ) as http:
        async with streamable_http_client(
            "http://127.0.0.1:8000/mcp", http_client=http
        ) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools = await session.list_tools()
    assert {tool.name for tool in tools.tools} == {
        "accounts_list", "account_status", "idle_preview"
    }

asyncio.run(smoke())
PY
docker rm --force placegame-p2
git diff --check
git status --short --branch
```

Expected: local non-integration suite passes, Pyright has zero errors, remote integration reports zero skips, the image becomes healthy, unauthorized `/mcp` is the exact 401, authenticated initialize succeeds, and production lists exactly three tools.

- [ ] **Step 4: Commit and hand off once**

```powershell
git add .github/workflows/build-image.yml
git commit -m "ci: smoke test authenticated MCP image"
```

The Terra handoff reports four implementation commits, final HEAD, exact pass counts, zero remote skips, Docker smoke results, unchanged original dirty worktree, unchanged `live_contract_unverified`, and any unresolved correctness concern. Sol performs one complete-diff review using the recorded gate. The review ends with `Approved` or one finite fix list; at most one Terra fix pass and one focused Sol re-review follow.
