from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from placegame.config import Settings
from placegame.mcp.auth import StaticBearerAuthMiddleware


VALID_TOKEN = "A" * 43


class SentinelApp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, scope: dict[str, Any], receive: Callable[[], Awaitable[dict[str, Any]]], send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self.calls.append(scope["method"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def raw_asgi_request(
    app: Any,
    *,
    method: str = "POST",
    headers: list[tuple[bytes, bytes]] | None = None,
    receive: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    scope_type: str = "http",
) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def default_receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def capture(message: dict[str, Any]) -> None:
        sent.append(message)

    scope: dict[str, Any] = {"type": scope_type, "headers": headers or []}
    if scope_type == "http":
        scope.update(
            {
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": "/mcp",
                "raw_path": b"/mcp",
                "query_string": b"",
                "client": ("127.0.0.1", 1234),
                "server": ("testserver", 80),
            }
        )
    else:
        scope["method"] = method
    await app(scope, receive or default_receive, capture)
    return sent


def response_tuple(sent: list[dict[str, Any]]) -> tuple[int, bytes, bytes, bytes]:
    start, body = sent
    headers = dict(start["headers"])
    return (
        start["status"],
        body["body"],
        headers[b"www-authenticate"],
        headers[b"content-type"],
    )


@pytest.mark.parametrize("line_ending", ["", "\n", "\r\n"])
def test_file_token_accepts_one_optional_line_ending(tmp_path: Path, line_ending: str):
    path = tmp_path / "mcp-token"
    path.write_bytes((VALID_TOKEN + line_ending).encode("ascii"))
    assert Settings(mcp_token_file=path).read_mcp_token().get_secret_value() == VALID_TOKEN


def test_environment_token_precedes_file(tmp_path: Path):
    path = tmp_path / "mcp-token"
    path.write_text("B" * 43, encoding="ascii")
    settings = Settings(mcp_token=SecretStr(VALID_TOKEN), mcp_token_file=path)
    assert settings.read_mcp_token().get_secret_value() == VALID_TOKEN


@pytest.mark.parametrize(
    "value",
    ["", "A" * 42, "A" * 44, "A" * 42 + "=", "A" * 42 + "+", "é" * 43, VALID_TOKEN + "\n"],
)
def test_invalid_token_fails_without_echo(value: str):
    with pytest.raises(ValueError) as caught:
        Settings(mcp_token=SecretStr(value)).read_mcp_token()
    if value:
        assert value not in str(caught.value)


@pytest.mark.parametrize("contents", [None, b"", b"\n", b"A" * 43 + b"\n\n", b"\xff" * 43])
def test_unavailable_file_token_fails_with_safe_message(tmp_path: Path, contents: bytes | None):
    path = tmp_path / "mcp-token"
    if contents is not None:
        path.write_bytes(contents)
    with pytest.raises(ValueError, match="^MCP token secret is unavailable$"):
        Settings(mcp_token_file=path).read_mcp_token()


def test_unreadable_file_token_fails_with_safe_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "mcp-token"
    path.write_text(VALID_TOKEN, encoding="ascii")

    def unavailable_read_bytes(self: Path) -> bytes:
        raise OSError("do not disclose")

    monkeypatch.setattr(Path, "read_bytes", unavailable_read_bytes)
    with pytest.raises(ValueError, match="^MCP token secret is unavailable$"):
        Settings(mcp_token_file=path).read_mcp_token()


def test_default_allowed_hosts_are_loopback_only():
    assert Settings().mcp_allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"]


@pytest.mark.parametrize("hosts", [[], [""], ["https://example.com"], ["example.com/mcp"], ["*.example.com"], ["example.*"], ["*:*"], ["example.com:8080"], [" example.com"], ["example.com "], ["example.com\t"]])
def test_invalid_allowed_hosts_are_rejected(hosts: list[str]):
    with pytest.raises(ValueError):
        Settings(mcp_allowed_hosts=hosts)


@pytest.mark.parametrize("hosts", [["testserver"], ["example.com:*"], ["127.0.0.1:*"], ["[::1]:*"]])
def test_exact_hosts_and_sdk_host_wildcards_are_accepted(hosts: list[str]):
    assert Settings(mcp_allowed_hosts=hosts).mcp_allowed_hosts == hosts


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Basic " + VALID_TOKEN.encode())],
        [(b"authorization", b"Bearer")],
        [(b"authorization", b"Bearer wrong")],
        [(b"authorization", b"Bearer " + VALID_TOKEN.encode())] * 2,
        [(b"authorization", b"Bearer \xff")],
    ],
)
async def test_auth_failures_are_identical_and_do_not_read_body(headers: list[tuple[bytes, bytes]]):
    child = SentinelApp()

    async def forbidden_receive() -> dict[str, Any]:
        raise AssertionError("unauthorized body was read")

    sent = await raw_asgi_request(
        StaticBearerAuthMiddleware(child, VALID_TOKEN),
        headers=headers,
        receive=forbidden_receive,
    )
    assert child.calls == []
    assert response_tuple(sent) == (
        401,
        b'{"error":"unauthorized"}',
        b"Bearer",
        b"application/json",
    )


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
async def test_valid_token_forwards_all_mcp_methods(method: str, scheme: str):
    child = SentinelApp()
    sent = await raw_asgi_request(
        StaticBearerAuthMiddleware(child, VALID_TOKEN),
        method=method,
        headers=[(b"authorization", f"{scheme} {VALID_TOKEN}".encode())],
    )
    assert child.calls == [method]
    assert sent[0]["status"] == 204


async def test_non_http_scope_passes_through_unchanged():
    child = SentinelApp()
    await raw_asgi_request(StaticBearerAuthMiddleware(child, VALID_TOKEN), scope_type="websocket")
    assert child.calls == ["POST"]
