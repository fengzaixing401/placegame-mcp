from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from placegame.application.errors import ApplicationError
from placegame.contracts import Actor
from placegame.errors import (
    AccountDisabled,
    AccountIdentityConflict,
    AccountNotFound,
    AccountPaused,
    AccountRemoved,
    AuthenticationRequired,
    AmbiguousMutation,
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
from placegame.models import AdminSession

from .auth import (
    DEFAULT_ABSOLUTE_SECONDS,
    AdminAuthError,
    AdminAuthService,
    PasswordTooShort,
    Unauthorized,
)
from .dependencies import SESSION_COOKIE_NAME, require_admin


WEBUI_ACTOR = Actor("webui", "operator", frozenset())


class SafeValidationRoute(APIRoute):
    """Keep request validation failures from echoing submitted secrets."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse({"error": "invalid_request"}, status_code=422)

        return safe_handler


class PasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    password: str = Field(min_length=1)


_ERROR_CODES: tuple[tuple[type[Exception], str, int], ...] = (
    (AccountNotFound, "account_not_found", 404),
    (AccountIdentityConflict, "account_identity_conflict", 409),
    (AccountDisabled, "account_disabled", 409),
    (AccountPaused, "account_paused", 409),
    (AccountRemoved, "account_removed", 409),
    (AuthenticationRequired, "authentication_required", 409),
    (PolicyUnavailable, "policy_unavailable", 409),
    (ReconciliationRequired, "reconciliation_required", 409),
    (PlanPreconditionFailed, "plan_precondition_failed", 409),
    (SessionRejected, "session_rejected", 409),
    (ContractChanged, "game_contract_changed", 409),
    (GameSchemaMismatch, "game_contract_changed", 409),
    (InventoryFull, "inventory_full", 409),
    (InsufficientResource, "insufficient_resource", 409),
    (GameConflict, "game_conflict", 409),
    (GameRateLimited, "game_rate_limited", 429),
    (AmbiguousMutation, "ambiguous_mutation", 409),
    (GameUnavailable, "game_unavailable", 503),
    (GameHttpError, "game_unavailable", 503),
)


def create_admin_router(*, cookie_secure: bool = True) -> APIRouter:
    router = APIRouter(prefix="/api/admin/v1", route_class=SafeValidationRoute)

    def auth(request: Request) -> AdminAuthService:
        return request.app.state.admin_auth

    def unauthorized() -> JSONResponse:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    def error_response(error: Exception) -> JSONResponse:
        if isinstance(error, AdminAuthError):
            status = 401 if isinstance(error, Unauthorized) else 409
            if isinstance(error, PasswordTooShort):
                status = 422
            return JSONResponse({"error": error.code}, status_code=status)
        if isinstance(error, ApplicationError):
            return JSONResponse({"error": error.code}, status_code=409)
        for kind, code, status in _ERROR_CODES:
            if isinstance(error, kind):
                return JSONResponse({"error": code}, status_code=status)
        return JSONResponse({"error": "internal_error"}, status_code=500)

    def same_origin(request: Request) -> JSONResponse | None:
        origin = request.headers.get("origin")
        if origin is None:
            return None
        parsed = urlsplit(origin)
        expected_netloc = request.url.netloc.lower()
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.netloc.lower() != expected_netloc
        ):
            return JSONResponse({"error": "origin_forbidden"}, status_code=403)
        return None

    def require_json(request: Request) -> JSONResponse | None:
        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("application/json"):
            return JSONResponse({"error": "content_type_required"}, status_code=415)
        return None

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            SESSION_COOKIE_NAME,
            token,
            max_age=DEFAULT_ABSOLUTE_SECONDS,
            httponly=True,
            secure=cookie_secure,
            samesite="strict",
            path="/",
        )

    def clear_session_cookie(response: Response) -> None:
        response.delete_cookie(
            SESSION_COOKIE_NAME,
            path="/",
            secure=cookie_secure,
            httponly=True,
            samesite="strict",
        )

    async def invoke(operation: Callable[[], Awaitable[Any]]) -> Any | JSONResponse:
        try:
            return await operation()
        except Exception as error:
            return error_response(error)

    @router.get("/auth/status", response_model=None)
    async def auth_status(request: Request) -> dict[str, bool] | JSONResponse:
        service = auth(request)
        setup_result = await invoke(service.is_setup)
        if isinstance(setup_result, JSONResponse):
            return setup_result
        setup_required = not setup_result
        session = None if setup_required else await require_admin(request)
        if isinstance(session, JSONResponse):
            return session
        return {"setupRequired": setup_required, "authenticated": session is not None}

    @router.post("/auth/setup", status_code=201, response_model=None)
    async def auth_setup(request: Request, body: PasswordRequest) -> dict[str, bool] | JSONResponse:
        origin_error = same_origin(request)
        if origin_error is not None:
            return origin_error
        content_error = require_json(request)
        if content_error is not None:
            return content_error
        result = await invoke(lambda: auth(request).setup(body.password))
        if isinstance(result, JSONResponse):
            return result
        return {"setupRequired": False, "authenticated": False}

    @router.post("/auth/login", response_model=None)
    async def auth_login(
        request: Request, response: Response, body: PasswordRequest
    ) -> dict[str, bool] | JSONResponse:
        origin_error = same_origin(request)
        if origin_error is not None:
            return origin_error
        content_error = require_json(request)
        if content_error is not None:
            return content_error
        result = await invoke(lambda: auth(request).login(body.password))
        if isinstance(result, JSONResponse):
            return result
        set_session_cookie(response, result.token)
        return {"authenticated": True}

    @router.post("/auth/logout", status_code=204, response_model=None)
    async def auth_logout(
        request: Request,
        response: Response,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Response | JSONResponse:
        origin_error = same_origin(request)
        if origin_error is not None:
            return origin_error
        content_error = require_json(request)
        if content_error is not None:
            return content_error
        if isinstance(session, JSONResponse):
            return session
        if session is None:
            return unauthorized()
        result = await invoke(
            lambda: auth(request).logout(request.cookies.get(SESSION_COOKIE_NAME))
        )
        if isinstance(result, JSONResponse):
            return result
        clear_session_cookie(response)
        response.status_code = 204
        return response

    @router.get("/accounts", response_model=None)
    async def accounts_list(
        request: Request,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        if isinstance(session, JSONResponse):
            return session
        if session is None:
            return unauthorized()
        result = await invoke(lambda: request.app.state.account_status_query.list())
        if isinstance(result, JSONResponse):
            return result
        return [item.model_dump(mode="json", by_alias=True) for item in result]

    @router.get("/accounts/{account_id}/status", response_model=None)
    async def account_status(
        account_id: UUID,
        request: Request,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        if isinstance(session, JSONResponse):
            return session
        if session is None:
            return unauthorized()
        result = await invoke(
            lambda: request.app.state.account_status_query.get(
                account_id, actor=WEBUI_ACTOR
            )
        )
        if isinstance(result, JSONResponse):
            return result
        return result.model_dump(mode="json", by_alias=True)

    @router.get("/accounts/{account_id}/idle-preview", response_model=None)
    async def idle_preview(
        account_id: UUID,
        request: Request,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        if isinstance(session, JSONResponse):
            return session
        if session is None:
            return unauthorized()
        correlation_id = uuid4().hex
        result = await invoke(
            lambda: request.app.state.idle_plan_use_case.preview(
                account_id, actor=WEBUI_ACTOR, correlation_id=correlation_id
            )
        )
        if isinstance(result, JSONResponse):
            return result
        return result.model_dump(mode="json", by_alias=True)

    @router.api_route(
        "",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def admin_prefix_not_found() -> JSONResponse:
        return JSONResponse({"error": "not_found"}, status_code=404)

    @router.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def admin_not_found() -> JSONResponse:
        return JSONResponse({"error": "not_found"}, status_code=404)

    return router
