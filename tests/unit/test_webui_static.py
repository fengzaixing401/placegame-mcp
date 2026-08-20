import httpx
from pydantic import SecretStr

from placegame.app import create_app


def web_settings(settings):
    return settings.model_copy(
        update={
            "mcp_token": SecretStr("A" * 43),
            "mcp_allowed_hosts": ["testserver"],
            "admin_cookie_secure": False,
        }
    )


async def test_root_and_assets_are_public_webui_resources(settings):
    transport = httpx.ASGITransport(app=create_app(web_settings(settings)))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        root = await client.get("/")
        css = await client.get("/assets/style.css")
        script = await client.get("/assets/app.js")

    assert root.status_code == 200
    assert root.headers["content-type"].startswith("text/html")
    assert "PlaceGame" in root.text
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert script.status_code == 200
    assert "api/admin/v1" in script.text


async def test_mcp_cookie_boundary_remains_bearer_only(settings):
    transport = httpx.ASGITransport(app=create_app(web_settings(settings)))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("placegame_session", "A" * 43)
        response = await client.post("/mcp")

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
