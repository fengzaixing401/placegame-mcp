from fastapi import FastAPI

from .config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="PlaceGame MCP", docs_url=None, redoc_url=None)
    app.state.settings = settings or Settings.from_env()

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    return app
