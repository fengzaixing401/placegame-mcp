"""MCP transport integration for PlaceGame."""

from .adapter import create_mcp_server
from .auth import StaticBearerAuthMiddleware

__all__ = ["StaticBearerAuthMiddleware", "create_mcp_server"]
