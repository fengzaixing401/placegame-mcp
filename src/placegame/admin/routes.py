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

from placegame.accounts.service import ManagedAccount
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


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    current_password: str = Field(alias="currentPassword", min_length=1)
    new_password: str = Field(alias="newPassword", min_length=1)


class CredentialsCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=8192)


class TokenOnlyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    label: str = Field(min_length=1, max_length=120)
    session_token: str = Field(alias="sessionToken", min_length=1, max_length=8192)


class LabelUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str = Field(min_length=1, max_length=120)


class CredentialsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    username: str | None = Field(default=None, max_length=256)
    password: str = Field(min_length=1, max_length=8192)


class TokenOnlyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    session_token: str = Field(alias="sessionToken", min_length=1, max_length=8192)


class PauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str = Field(min_length=1, max_length=256)


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
        if isinstance(error, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=422)
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

    def write_guard(request: Request) -> JSONResponse | None:
        origin_error = same_origin(request)
        if origin_error is not None:
            return origin_error
        return require_json(request)

    def account_payload(account: ManagedAccount) -> dict[str, Any]:
        return {
            "account_id": account.id,
            "label": account.label,
            "auth_mode": account.auth_mode,
            "enabled": account.enabled,
            "paused_reason": account.paused_reason,
            "session_expires_at": account.session_expires_at,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
        }

    def account_response(account: ManagedAccount) -> dict[str, Any]:
        return {"account": account_payload(account)}

    def account_list_payload(account: ManagedAccount) -> dict[str, Any]:
        payload = account_payload(account)
        payload["auth_state"] = (
            "required" if account.paused_reason == "authentication_required" else "unknown"
        )
        return payload

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

    @router.patch("/auth/password", status_code=204, response_model=None)
    async def auth_change_password(
        request: Request,
        response: Response,
        body: PasswordChangeRequest,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Response | JSONResponse:
        guard = write_guard(request)
        if guard is not None:
            return guard
        if isinstance(session, JSONResponse):
            return session
        if session is None:
            return unauthorized()
        result = await invoke(
            lambda: auth(request).change_password(
                body.current_password, body.new_password
            )
        )
        if isinstance(result, JSONResponse):
            return result
        # change_password drops every session, so this browser must sign in again.
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
        if hasattr(request.app.state, "account_service"):
            result = await invoke(
                request.app.state.account_service.list_accounts
            )
            if isinstance(result, JSONResponse):
                return result
            return [account_list_payload(item) for item in result]
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

    async def authenticated_write(
        request: Request,
        session: AdminSession | JSONResponse | None,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any | JSONResponse:
        access_error = write_access(request, session)
        if access_error is not None:
            return access_error
        return await invoke(operation)

    def write_access(
        request: Request, session: AdminSession | JSONResponse | None
    ) -> JSONResponse | None:
        guard = write_guard(request)
        if guard is not None:
            return guard
        if isinstance(session, JSONResponse):
            return session
        if session is None:
            return unauthorized()
        return None

    async def account_after(
        request: Request,
        account_id: UUID,
        session: AdminSession | JSONResponse | None,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        result = await authenticated_write(request, session, operation)
        if isinstance(result, JSONResponse):
            return result
        account = await invoke(
            lambda: request.app.state.account_service.get(account_id)
        )
        if isinstance(account, JSONResponse):
            return account
        return account_response(account)

    async def require_account_mode(
        request: Request, account_id: UUID, expected: str
    ) -> JSONResponse | None:
        account = await invoke(
            lambda: request.app.state.account_service.get(account_id)
        )
        if isinstance(account, JSONResponse):
            return account
        if account.auth_mode != expected:
            return JSONResponse(
                {"error": "account_auth_mode_conflict"}, status_code=409
            )
        return None

    @router.post("/accounts/credentials", status_code=201, response_model=None)
    async def accounts_create_credentials(
        request: Request,
        body: CredentialsCreateRequest,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        result = await authenticated_write(
            request,
            session,
            lambda: request.app.state.account_service.add_credentials(
                body.label, body.username, body.password, actor=WEBUI_ACTOR
            ),
        )
        if isinstance(result, JSONResponse):
            return result
        return account_response(result)

    @router.post("/accounts/token-only", status_code=201, response_model=None)
    async def accounts_create_token_only(
        request: Request,
        body: TokenOnlyCreateRequest,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        result = await authenticated_write(
            request,
            session,
            lambda: request.app.state.account_service.add_token_only(
                body.label, body.session_token, actor=WEBUI_ACTOR
            ),
        )
        if isinstance(result, JSONResponse):
            return result
        return account_response(result)

    @router.patch("/accounts/{account_id}/label", response_model=None)
    async def account_label_update(
        account_id: UUID,
        request: Request,
        body: LabelUpdateRequest,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        return await account_after(
            request,
            account_id,
            session,
            lambda: request.app.state.account_service.update_label(
                account_id, body.label, actor=WEBUI_ACTOR
            ),
        )

    @router.patch("/accounts/{account_id}/credentials", response_model=None)
    async def account_credentials_update(
        account_id: UUID,
        request: Request,
        body: CredentialsUpdateRequest,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        access_error = write_access(request, session)
        if access_error is not None:
            return access_error
        mode_error = await require_account_mode(request, account_id, "credentials")
        if mode_error is not None:
            return mode_error
        return await account_after(
            request,
            account_id,
            session,
            lambda: request.app.state.account_service.update_credentials(
                account_id, body.username, body.password, actor=WEBUI_ACTOR
            ),
        )

    @router.patch("/accounts/{account_id}/token-only", response_model=None)
    async def account_token_update(
        account_id: UUID,
        request: Request,
        body: TokenOnlyUpdateRequest,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        access_error = write_access(request, session)
        if access_error is not None:
            return access_error
        mode_error = await require_account_mode(request, account_id, "token_only")
        if mode_error is not None:
            return mode_error
        return await account_after(
            request,
            account_id,
            session,
            lambda: request.app.state.account_service.update_token_only(
                account_id, body.session_token, actor=WEBUI_ACTOR
            ),
        )

    @router.post("/accounts/{account_id}/enable", response_model=None)
    async def account_enable(
        account_id: UUID,
        request: Request,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        return await account_after(
            request,
            account_id,
            session,
            lambda: request.app.state.account_service.enable(
                account_id, actor=WEBUI_ACTOR
            ),
        )

    @router.post("/accounts/{account_id}/disable", response_model=None)
    async def account_disable(
        account_id: UUID,
        request: Request,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        return await account_after(
            request,
            account_id,
            session,
            lambda: request.app.state.account_service.disable(
                account_id, actor=WEBUI_ACTOR
            ),
        )

    @router.post("/accounts/{account_id}/pause", response_model=None)
    async def account_pause(
        account_id: UUID,
        request: Request,
        body: PauseRequest,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        return await account_after(
            request,
            account_id,
            session,
            lambda: request.app.state.account_service.pause(
                account_id, body.reason, actor=WEBUI_ACTOR
            ),
        )

    @router.post("/accounts/{account_id}/resume", response_model=None)
    async def account_resume(
        account_id: UUID,
        request: Request,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        return await account_after(
            request,
            account_id,
            session,
            lambda: request.app.state.account_service.resume(
                account_id, actor=WEBUI_ACTOR
            ),
        )

    @router.delete("/accounts/{account_id}", response_model=None)
    async def account_remove(
        account_id: UUID,
        request: Request,
        session: AdminSession | JSONResponse | None = Depends(require_admin),
    ) -> Any:
        return await account_after(
            request,
            account_id,
            session,
            lambda: request.app.state.account_service.disable_drain_remove(
                account_id, actor=WEBUI_ACTOR
            ),
        )

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
