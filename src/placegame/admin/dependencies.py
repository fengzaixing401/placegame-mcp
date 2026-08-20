from fastapi import Request

from placegame.models import AdminSession

from .auth import SESSION_TOKEN_PATTERN

SESSION_COOKIE_NAME = "placegame_session"


async def require_admin(request: Request) -> AdminSession | None:
    """Return the current session, or None for the route's stable 401 response."""

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None or SESSION_TOKEN_PATTERN.fullmatch(token) is None:
        return None
    return await request.app.state.admin_auth.validate(token)
