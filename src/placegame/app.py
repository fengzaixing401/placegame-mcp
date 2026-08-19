from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Response
from sqlalchemy import text

from .accounts.repository import AccountRepository
from .accounts.service import AccountService
from .application.idle import IdleExecutionClaims, IdleExecutionGuard, IdleExecuteUseCase, IdlePlanUseCase, IdlePreviewStore
from .application.status import AccountStatusQuery
from .config import Settings
from .db import Database
from .game.client import HttpGameClient
from .policy.store import PostgresPolicyService
from .security.crypto import SecretBox


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="PlaceGame MCP", docs_url=None, redoc_url=None)
    app.state.settings = settings or Settings.from_env()
    app.state.secret_box = SecretBox(app.state.settings.read_master_key_b64().get_secret_value())
    app.state.database = Database.from_settings(app.state.settings)
    app.state.session_factory = app.state.database.sessions
    app.state.http_client = httpx.AsyncClient()
    repository = AccountRepository()

    def game_factory(session_token: str | None) -> HttpGameClient:
        return HttpGameClient(app.state.settings, session_token=session_token, http_client=app.state.http_client)

    policy = PostgresPolicyService(app.state.database.sessions, lambda _account_id: 0, repository)
    accounts = AccountService(
        app.state.database.sessions,
        app.state.secret_box,
        game_factory,
        policy_provider=policy,
        repository=repository,
    )
    app.state.account_repository = repository
    app.state.policy_service = policy
    app.state.account_service = accounts
    app.state.idle_preview_store = IdlePreviewStore(app.state.database.sessions, repository)
    app.state.idle_execution_guard = IdleExecutionGuard(app.state.database.sessions)
    app.state.idle_execution_claims = IdleExecutionClaims(app.state.database.sessions, repository)
    app.state.account_status_query = AccountStatusQuery(accounts)
    app.state.idle_plan_use_case = IdlePlanUseCase(accounts, app.state.idle_preview_store)
    app.state.idle_execute_use_case = IdleExecuteUseCase(accounts, app.state.idle_execution_guard, app.state.idle_execution_claims)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await _app.state.http_client.aclose()
            await _app.state.database.aclose()

    app.router.lifespan_context = lifespan

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready(response: Response) -> dict[str, str]:
        try:
            async with app.state.database.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            response.status_code = 503
            return {"status": "unavailable"}
        return {"status": "ok"}

    return app
