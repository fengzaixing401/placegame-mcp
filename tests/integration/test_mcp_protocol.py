from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from alembic import command
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from placegame.application.idle import IdleExecutionClaims, IdleExecutionGuard, IdleExecuteUseCase, IdlePlanUseCase, IdlePreviewStore
from placegame.application.status import AccountStatusQuery
from placegame.mcp.adapter import create_mcp_server
from placegame.mcp.auth import StaticBearerAuthMiddleware
from tests.unit.test_accounts import ServiceEnvironment


MCP_TOKEN = "A" * 43


@pytest.fixture
def mcp_database_url(postgres_url, alembic_config):
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture
async def mcp_environment(mcp_database_url, secret_box):
    engine = create_async_engine(mcp_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE audit_events, job_runs, jobs, action_plans, "
                "account_snapshots, account_policies, game_accounts RESTART IDENTITY CASCADE"
            )
        )
    environment = ServiceEnvironment(sessions, secret_box, engine)
    try:
        yield environment
    finally:
        await engine.dispose()


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
