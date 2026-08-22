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
    assert "添加游戏账号" in root.text
    # The account already exists in the game; this console only takes it under
    # management, so the copy must not promise to create one.
    assert "创建游戏账号" not in root.text
    assert 'lang="zh-CN"' in root.text
    assert "sessionToken" in script.text
    assert "/accounts/credentials" in script.text
    assert "/accounts/token-only" in script.text
    for action in ("enable", "disable", "pause", "resume"):
        assert f'"{action}"' in script.text
    assert "disable_drain_remove" not in script.text
    assert 'editSecret.type = "password"' in script.text
    assert 'edit-dialog' in root.text
    assert 'password-dialog' in root.text
    assert "修改管理员密码" in root.text
    assert "/auth/password" in script.text
    assert "password_too_short" in script.text
    assert "setup_already_complete" in script.text
    # A contract mismatch is deterministic, so the copy must not suggest retrying.
    assert "请刷新后重试" not in script.text
    assert 'window.prompt("New game password' not in script.text
    assert 'window.prompt("New session token' not in script.text


async def test_mcp_cookie_boundary_remains_bearer_only(settings):
    transport = httpx.ASGITransport(app=create_app(web_settings(settings)))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("placegame_session", "A" * 43)
        response = await client.post("/mcp")

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


async def test_composed_app_keeps_admin_api_ahead_of_mcp_fallback(settings):
    class AuthStub:
        async def is_setup(self):
            return False

        async def validate(self, _token):
            return None

    app = create_app(web_settings(settings))
    app.state.admin_auth = AuthStub()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        status = await client.get("/api/admin/v1/auth/status")
        prefix = await client.get("/api/admin/v1")
        unknown = await client.get("/api/admin/v1/does-not-exist")

    assert status.status_code == 200
    assert status.json() == {"setupRequired": True, "authenticated": False}
    assert prefix.status_code == 404
    assert prefix.json() == {"error": "not_found"}
    assert unknown.status_code == 404
    assert unknown.json() == {"error": "not_found"}
