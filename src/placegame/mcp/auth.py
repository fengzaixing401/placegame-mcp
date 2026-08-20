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
