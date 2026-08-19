from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import re

from mcp.server.fastmcp.exceptions import ToolError

from placegame.application.errors import ApplicationError
from placegame.application.models import (
    AccountStatus,
    AccountSummary,
    IdleExecution,
    IdlePreview,
    IdleState,
)
from placegame.contracts import Actor
from placegame.mcp.adapter import MCP_ACTOR, create_mcp_server
from placegame.errors import (
    AccountDisabled,
    AccountIdentityConflict,
    AccountNotFound,
    AccountPaused,
    AccountRemoved,
    AmbiguousMutation,
    AuthenticationRequired,
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


@dataclass(frozen=True)
class PreviewCall:
    actor: Actor
    correlation_id: str


@dataclass(frozen=True)
class ExecuteCall:
    account_id: UUID
    plan_id: UUID
    actor: Actor


@dataclass
class ServerFakes:
    account_id: UUID
    status: StatusFake
    preview: PreviewFake
    execute: ExecuteFake


class StatusFake:
    def __init__(self) -> None:
        self.account_id = uuid4()
        self.actors: list[Actor] = []

    async def list(self) -> tuple[AccountSummary, ...]:
        return (AccountSummary(account_id=self.account_id, label="A", enabled=True, paused_reason=None, auth_state="unknown"),)

    async def get(self, account_id: UUID, *, actor: Actor) -> AccountStatus:
        self.actors.append(actor)
        return AccountStatus(
            account=(await self.list())[0],
            bootstrap_account_id="a",
            idle=IdleState(accumulated_seconds=0, capacity_seconds=1),
            fetched_at=datetime.now(UTC),
        )


class PreviewFake:
    def __init__(self, account_id: UUID) -> None:
        self.calls: list[PreviewCall] = []
        self.account_id = account_id

    async def preview(self, account_id: UUID, *, actor: Actor, correlation_id: str) -> IdlePreview:
        self.calls.append(PreviewCall(actor=actor, correlation_id=correlation_id))
        return IdlePreview(account_id=account_id, plan_id=None, decision="wait", accumulated_seconds=0, capacity_seconds=1, threshold_seconds=1, expires_at=None, reason="ok", correlation_id=correlation_id)


class ExecuteFake:
    def __init__(self) -> None:
        self.calls: list[ExecuteCall] = []

    async def execute(self, account_id: UUID, plan_id: UUID, *, actor: Actor, correlation_id: str) -> IdleExecution:
        self.calls.append(ExecuteCall(account_id=account_id, plan_id=plan_id, actor=actor))
        return IdleExecution(account_id=account_id, plan_id=plan_id, status="executed", applied=True, reconciled=False, collected=True, correlation_id=correlation_id)


def build_server(*, test_mode: bool) -> tuple[object, ServerFakes]:
    status = StatusFake()
    preview = PreviewFake(status.account_id)
    execute = ExecuteFake()
    server = create_mcp_server(status, preview, execute, test_mode=test_mode, allowed_hosts=[])
    return server, ServerFakes(status.account_id, status, preview, execute)


@pytest.mark.asyncio
async def test_production_surface_and_transport_settings():
    server, _ = build_server(test_mode=False)
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


@pytest.mark.asyncio
async def test_delegation_uses_fixed_actor_and_fresh_correlations():
    server, fakes = build_server(test_mode=False)
    await server.call_tool("account_status", {"account_id": str(fakes.account_id)})
    await server.call_tool("idle_preview", {"account_id": str(fakes.account_id)})
    await server.call_tool("idle_preview", {"account_id": str(fakes.account_id)})
    assert fakes.status.actors == [MCP_ACTOR]
    assert all(call.actor == MCP_ACTOR for call in fakes.preview.calls)
    assert all(re.fullmatch(r"[0-9a-f]{32}", call.correlation_id) for call in fakes.preview.calls)
    assert fakes.preview.calls[0].correlation_id != fakes.preview.calls[1].correlation_id


@pytest.mark.asyncio
async def test_test_mode_alone_registers_real_execute_signature():
    server, fakes = build_server(test_mode=True)
    plan_id = uuid4()
    assert {tool.name for tool in await server.list_tools()} == {"accounts_list", "account_status", "idle_preview", "idle_execute"}
    await server.call_tool("idle_execute", {"account_id": str(fakes.account_id), "plan_id": str(plan_id)})
    assert fakes.execute.calls[0].account_id == fakes.account_id
    assert fakes.execute.calls[0].plan_id == plan_id
    assert fakes.execute.calls[0].actor == MCP_ACTOR


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ApplicationError("custom_code"), "custom_code"),
        (AccountNotFound(), "account_not_found"),
        (AccountIdentityConflict(), "account_identity_conflict"),
        (AccountDisabled(), "account_disabled"),
        (AccountPaused(), "account_paused"),
        (AccountRemoved(), "account_removed"),
        (AuthenticationRequired(), "authentication_required"),
        (PolicyUnavailable(), "policy_unavailable"),
        (ReconciliationRequired(), "reconciliation_required"),
        (PlanPreconditionFailed(), "plan_precondition_failed"),
        (SessionRejected(), "session_rejected"),
        (ContractChanged(), "game_contract_changed"),
        (
            GameSchemaMismatch(
                "schema", {"Authorization": "Bearer never-log", "cookie": "cookie-marker"}
            ),
            "game_contract_changed",
        ),
        (InventoryFull(), "inventory_full"),
        (InsufficientResource({"database": "database-marker"}), "insufficient_resource"),
        (GameConflict(), "game_conflict"),
        (GameRateLimited(), "game_rate_limited"),
        (AmbiguousMutation("mutation"), "ambiguous_mutation"),
        (GameUnavailable("unavailable"), "game_unavailable"),
        (GameHttpError("http", {"raw_body": "raw-body-marker"}), "game_unavailable"),
        (
            RuntimeError(
                "Authorization: Bearer never-log cookie-marker database-marker raw-body-marker"
            ),
            "internal_error",
        ),
    ],
)
async def test_errors_have_stable_codes_without_leaking_details(
    error: Exception, code: str, caplog: pytest.LogCaptureFixture
) -> None:
    class FailingStatus(StatusFake):
        async def list(self):
            raise error

    status = FailingStatus()
    server = create_mcp_server(status, PreviewFake(status.account_id), ExecuteFake(), test_mode=False, allowed_hosts=[])
    with pytest.raises(ToolError) as caught:
        await server.call_tool("accounts_list", {})
    assert str(caught.value) == f"Error executing tool accounts_list: {code}"
    rendered = caplog.text
    assert "mcp_tool_failed" in rendered
    if type(error) is RuntimeError:
        assert code == "internal_error"
    for marker in ("never-log", "cookie-marker", "database-marker", "raw-body-marker"):
        assert marker not in str(caught.value)
        assert marker not in rendered
        assert not any(_contains_marker(_record_values(record), marker) for record in caplog.records)
    assert "Traceback" not in rendered
    assert all(record.exc_info is None and record.stack_info is None for record in caplog.records)


_STANDARD_LOG_RECORD_FIELDS = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
)


def _record_values(record: logging.LogRecord) -> tuple[object, ...]:
    return (
        record.getMessage(),
        record.msg,
        record.args,
        *(value for key, value in record.__dict__.items() if key not in _STANDARD_LOG_RECORD_FIELDS),
    )


def _contains_marker(value: object, marker: str) -> bool:
    if isinstance(value, str):
        return marker in value
    if isinstance(value, dict):
        return any(
            _contains_marker(key, marker) or _contains_marker(item, marker)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_marker(item, marker) for item in value)
    return False
