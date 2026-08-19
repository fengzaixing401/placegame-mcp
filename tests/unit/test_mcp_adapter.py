from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import re

from placegame.application.models import (
    AccountStatus,
    AccountSummary,
    IdleExecution,
    IdlePreview,
    IdleState,
)
from placegame.contracts import Actor
from placegame.mcp.adapter import MCP_ACTOR, create_mcp_server
from placegame.application.errors import ApplicationError
from placegame.errors import AccountNotFound, GameUnavailable


class StatusFake:
    def __init__(self) -> None:
        self.account_id = uuid4()
        self.status = type("StatusCalls", (), {"actors": []})()

    async def list(self) -> tuple[AccountSummary, ...]:
        return (AccountSummary(account_id=self.account_id, label="A", enabled=True, paused_reason=None, auth_state="unknown"),)

    async def get(self, account_id: UUID, *, actor: Actor) -> AccountStatus:
        self.status.actors.append(actor)
        return AccountStatus(account=await self.list().__anext__() if False else (await self.list())[0], bootstrap_account_id="a", idle=IdleState(accumulated_seconds=0, capacity_seconds=1), fetched_at=datetime.now(UTC))


class PreviewFake:
    def __init__(self, account_id: UUID) -> None:
        self.calls = []
        self.account_id = account_id

    async def preview(self, account_id: UUID, *, actor: Actor, correlation_id: str) -> IdlePreview:
        self.calls.append(type("Call", (), {"actor": actor, "correlation_id": correlation_id})())
        return IdlePreview(account_id=account_id, plan_id=None, decision="wait", accumulated_seconds=0, capacity_seconds=1, threshold_seconds=1, expires_at=None, reason="ok", correlation_id=correlation_id)


class ExecuteFake:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, account_id: UUID, plan_id: UUID, *, actor: Actor, correlation_id: str) -> IdleExecution:
        self.calls.append(type("Call", (), {"account_id": account_id, "plan_id": plan_id, "actor": actor})())
        return IdleExecution(account_id=account_id, plan_id=plan_id, status="executed", applied=True, reconciled=False, collected=True, correlation_id=correlation_id)


def build_server(*, test_mode: bool):
    status = StatusFake()
    preview = PreviewFake(status.account_id)
    execute = ExecuteFake()
    server = create_mcp_server(status, preview, execute, test_mode=test_mode, allowed_hosts=[])
    return server, type("Fakes", (), {"account_id": status.account_id, "status": status, "preview": preview, "execute": execute})()


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
    assert fakes.status.status.actors == [MCP_ACTOR]
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
@pytest.mark.parametrize("error,code", [(ApplicationError("custom_code"), "custom_code"), (AccountNotFound(), "account_not_found"), (GameUnavailable("x"), "game_unavailable"), (RuntimeError("raw-body database cookie Authorization: Bearer never-log"), "internal_error")])
async def test_errors_have_stable_codes_without_leaking_details(error, code, caplog):
    class FailingStatus(StatusFake):
        async def list(self):
            raise error

    status = FailingStatus()
    server = create_mcp_server(status, PreviewFake(status.account_id), ExecuteFake(), test_mode=False, allowed_hosts=[])
    with pytest.raises(Exception) as caught:
        await server.call_tool("accounts_list", {})
    assert str(caught.value).endswith(code)
    rendered = " ".join(record.getMessage() for record in caplog.records)
    assert "mcp_tool_failed" in rendered
    if code == "internal_error":
        assert "never-log" not in rendered and "raw-body" not in rendered and "database" not in rendered
