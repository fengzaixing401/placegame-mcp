from fastapi import Request
from fastapi.responses import JSONResponse

from placegame.models import AdminSession

from .auth import SESSION_TOKEN_PATTERN

SESSION_COOKIE_NAME = "placegame_session"


async def require_admin(request: Request) -> AdminSession | JSONResponse | None:
    """Return the session, a stable internal error, or None when unauthenticated."""

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None or SESSION_TOKEN_PATTERN.fullmatch(token) is None:
        return None
    try:
        return await request.app.state.admin_auth.validate(token)
    except Exception:
        return JSONResponse({"error": "internal_error"}, status_code=500)
