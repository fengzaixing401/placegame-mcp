import json
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    json_body: Any


@dataclass(frozen=True)
class RegisteredResponse:
    status_code: int
    body: Any
    headers: Mapping[str, str]


_CREDENTIAL_KEY_PARTS = (
    "password",
    "auth",
    "token",
    "secret",
    "authorization",
    "cookie",
    "api-key",
    "api_key",
    "apikey",
    "credential",
)


def _redact_credentials(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]"
            if isinstance(key, str)
            and any(part in key.lower() for part in _CREDENTIAL_KEY_PARTS)
            else _redact_credentials(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_credentials(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_credentials(item) for item in value)
    return value


class FakeGameServer:
    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self._routes: dict[tuple[str, str], RegisteredResponse] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self._timeouts: dict[tuple[str, str], int] = {}
        self.timeout_delay_seconds = 0.2

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("fake game server is not running")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def register(
        self,
        method: str,
        path: str,
        body: Any,
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if not path.startswith("/api/"):
            raise ValueError("fake game routes must be registered under /api/")
        self._routes[(method.upper(), path)] = RegisteredResponse(
            status_code, body, dict(headers or {})
        )

    def timeout(self, method: str, path: str, *, count: int = 1) -> None:
        if not path.startswith("/api/"):
            raise ValueError("fake game timeout paths must be under /api/")
        if count < 1:
            raise ValueError("fake game timeout count must be positive")
        self._timeouts[(method.upper(), path)] = count

    def __enter__(self) -> "FakeGameServer":
        fake = self

        class RequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self._handle()

            def do_POST(self) -> None:  # noqa: N802
                self._handle()

            def do_PUT(self) -> None:  # noqa: N802
                self._handle()

            def do_DELETE(self) -> None:  # noqa: N802
                self._handle()

            def _handle(self) -> None:
                path = urlsplit(self.path).path
                content_length = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(content_length) if content_length else b""
                try:
                    json_body = json.loads(payload) if payload else None
                except json.JSONDecodeError:
                    json_body = None

                recorded_headers = _redact_credentials(
                    {key.lower(): value for key, value in self.headers.items()}
                )
                fake.requests.append(
                    RecordedRequest(
                        self.command,
                        path,
                        recorded_headers,
                        _redact_credentials(json_body),
                    )
                )

                route = (self.command, path)
                remaining_timeouts = fake._timeouts.get(route, 0)
                if remaining_timeouts:
                    if remaining_timeouts == 1:
                        del fake._timeouts[route]
                    else:
                        fake._timeouts[route] = remaining_timeouts - 1
                    time.sleep(fake.timeout_delay_seconds)

                response = fake._routes.get(route)
                if response is None:
                    self._send_json(404, {"detail": "not found"})
                    return
                self._send_json(response.status_code, response.body, response.headers)

            def _send_json(
                self,
                status_code: int,
                body: Any,
                headers: Mapping[str, str] | None = None,
            ) -> None:
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except BrokenPipeError:
                    pass

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join()
