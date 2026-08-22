from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar
from uuid import UUID, uuid4

from mcp.server.fastmcp import FastMCP  # type: ignore[reportMissingImports]
from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[reportMissingImports]
from mcp.server.transport_security import TransportSecuritySettings  # type: ignore[reportMissingImports]

from placegame.application.errors import ApplicationError
from placegame.application.models import AccountStatus, AccountSummary, IdleExecution, IdlePreview
from placegame.contracts import Actor
from placegame.errors import (
    AccountDisabled,
    AccountIdentityConflict,
    AccountNotFound,
    AccountPaused,
    AccountRemoved,
    AmbiguousMutation,
    AuthenticationRequired,
    ClientVersionRejected,
    ContractChanged,
    GameConflict,
    GameHttpError,
    GameRateLimited,
    GameSchemaMismatch,
    GameUnavailable,
    InsufficientResource,
    InventoryFull,
    PlanPreconditionFailed,
    PolicyUnavailable,
    ReconciliationRequired,
    SessionRejected,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


class AccountStatusQuery(Protocol):
    async def list(self) -> tuple[AccountSummary, ...]: ...
    async def get(self, account_id: UUID, *, actor: Actor) -> AccountStatus: ...


class IdlePlanUseCase(Protocol):
    async def preview(self, account_id: UUID, *, actor: Actor, correlation_id: str) -> IdlePreview: ...


class IdleExecuteUseCase(Protocol):
    async def execute(self, account_id: UUID, plan_id: UUID, *, actor: Actor, correlation_id: str) -> IdleExecution: ...


MCP_ACTOR = Actor("mcp", "operator", frozenset())

_ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (AccountNotFound, "account_not_found"),
    (AccountIdentityConflict, "account_identity_conflict"),
    (AccountDisabled, "account_disabled"),
    (AccountPaused, "account_paused"),
    (AccountRemoved, "account_removed"),
    (AuthenticationRequired, "authentication_required"),
    (PolicyUnavailable, "policy_unavailable"),
    (ReconciliationRequired, "reconciliation_required"),
    (PlanPreconditionFailed, "plan_precondition_failed"),
    (SessionRejected, "session_rejected"),
    (ClientVersionRejected, "game_client_version_rejected"),
    (ContractChanged, "game_contract_changed"),
    (GameSchemaMismatch, "game_contract_changed"),
    (InventoryFull, "inventory_full"),
    (InsufficientResource, "insufficient_resource"),
    (GameConflict, "game_conflict"),
    (GameRateLimited, "game_rate_limited"),
    (AmbiguousMutation, "ambiguous_mutation"),
    (GameUnavailable, "game_unavailable"),
    (GameHttpError, "game_unavailable"),
)


async def _invoke(
    operation: Callable[[], Awaitable[T]],
    *,
    tool_name: str,
    account_id: UUID | None,
    correlation_id: str,
) -> T:
    try:
        return await operation()
    except ApplicationError as exc:
        code = exc.code
    except Exception as exc:
        code = next((mapped for kind, mapped in _ERROR_CODES if isinstance(exc, kind)), "internal_error")
    else:
        raise AssertionError("unreachable")
    logger.error(
        "mcp_tool_failed",
        extra={"tool_name": tool_name, "account_id": str(account_id) if account_id is not None else None, "correlation_id": correlation_id, "code": code},
    )
    raise ToolError(code) from None


def create_mcp_server(
    status_query: AccountStatusQuery,
    idle_plan: IdlePlanUseCase,
    idle_execute_use_case: IdleExecuteUseCase,
    *,
    test_mode: bool,
    allowed_hosts: list[str],
) -> FastMCP:
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

    @server.tool(name="accounts_list", structured_output=True)
    async def accounts_list() -> tuple[AccountSummary, ...]:
        correlation_id = uuid4().hex
        return await _invoke(status_query.list, tool_name="accounts_list", account_id=None, correlation_id=correlation_id)

    @server.tool(name="account_status", structured_output=True)
    async def account_status(account_id: UUID) -> AccountStatus:
        correlation_id = uuid4().hex
        return await _invoke(lambda: status_query.get(account_id, actor=MCP_ACTOR), tool_name="account_status", account_id=account_id, correlation_id=correlation_id)

    @server.tool(name="idle_preview", structured_output=True)
    async def idle_preview(account_id: UUID) -> IdlePreview:
        correlation_id = uuid4().hex
        return await _invoke(lambda: idle_plan.preview(account_id, actor=MCP_ACTOR, correlation_id=correlation_id), tool_name="idle_preview", account_id=account_id, correlation_id=correlation_id)

    if test_mode:
        @server.tool(name="idle_execute", structured_output=True)
        async def idle_execute(account_id: UUID, plan_id: UUID) -> IdleExecution:
            correlation_id = uuid4().hex
            return await _invoke(lambda: idle_execute_use_case.execute(account_id, plan_id, actor=MCP_ACTOR, correlation_id=correlation_id), tool_name="idle_execute", account_id=account_id, correlation_id=correlation_id)

    return server
