from fastapi import FastAPI

from .config import Settings
from .db import get_session
from .security.crypto import SecretBox


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="PlaceGame MCP", docs_url=None, redoc_url=None)
    app.state.settings = settings or Settings.from_env()
    app.state.secret_box = SecretBox(app.state.settings.read_master_key_b64().get_secret_value())
    app.state.session_factory = get_session(app.state.settings)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    return app
