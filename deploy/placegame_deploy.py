import logging
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

IMAGE_REPOSITORY = "ghcr.io/fengzaixing401/placegame-mcp"
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
INSTALL_ROOT = Path("/opt/placegame-mcp")
PROJECT_NAME = "placegame-mcp"
COMPOSE_CONTROL_ENV = frozenset(
    {
        "PLACEGAME_IMAGE",
        "COMPOSE_FILE",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_PROJECT_DIRECTORY",
        "COMPOSE_PROFILES",
        "COMPOSE_ENV_FILES",
        "COMPOSE_PATH_SEPARATOR",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "DOCKER_DEFAULT_PLATFORM",
    }
)
logger = logging.getLogger(__name__)


def validate_digest(value: str) -> str:
    if DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid image digest")
    return value


@dataclass(frozen=True)
class ReleaseState:
    image: str


class CommandRunner(Protocol):
    def run(self, argv: tuple[str, ...]) -> None:
        raise NotImplementedError


class SubprocessRunner:
    def run(self, argv: tuple[str, ...]) -> None:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in COMPOSE_CONTROL_ENV
        }
        environment["DOCKER_HOST"] = "unix:///var/run/docker.sock"
        subprocess.run(argv, check=True, shell=False, env=environment)


class Deployer:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        root: Path = INSTALL_ROOT,
        health_probe: Callable[[str], bool] | None = None,
        health_attempts: int = 30,
    ) -> None:
        if not 1 <= health_attempts <= 30:
            raise ValueError("health_attempts must be between 1 and 30")
        self.runner = runner or SubprocessRunner()
        self.root = Path(root)
        self.health_probe = health_probe or self._probe
        self.health_attempts = health_attempts

    def _probe(self, url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return 200 <= response.status < 300
        except Exception:
            return False

    def _compose(self, env: Path, *args: str) -> tuple[str, ...]:
        return (
            "docker", "compose", "--project-name", PROJECT_NAME,
            "--project-directory", str(self.root), "--env-file", str(env),
            "--file", str(self.root / "deploy/compose.yaml"), *args,
        )

    def _read_current(self) -> str | None:
        path = self.root / "current.env"
        if not path.exists():
            return None
        text = path.read_text(encoding="ascii")
        lines = text.splitlines()
        if not text.endswith("\n") or len(lines) != 1:
            raise ValueError("invalid current state")
        prefix = "PLACEGAME_IMAGE="
        assignment = lines[0]
        if not assignment.startswith(prefix):
            raise ValueError("invalid current state")
        image = assignment[len(prefix):]
        if not image.startswith(IMAGE_REPOSITORY + "@"):
            raise ValueError("invalid current state")
        try:
            validate_digest(image.split("@", 1)[1])
        except ValueError as exc:
            raise ValueError("invalid current state") from exc
        return image

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="ascii")
        temporary.replace(path)

    def deploy(self, digest: str) -> None:
        image = IMAGE_REPOSITORY + "@" + validate_digest(digest)
        prior = self._read_current()
        candidate = self.root / "state/candidate.env"
        self._write(candidate, f"PLACEGAME_IMAGE={image}\n")
        self.runner.run(self._compose(candidate, "pull", "app", "migrate"))
        self.runner.run(self._compose(candidate, "up", "-d", "postgres"))
        self.runner.run(self._compose(candidate, "--profile", "tools", "run", "--rm", "migrate"))
        try:
            self.runner.run(self._compose(candidate, "up", "-d", "--no-deps", "app"))
            for attempt in range(self.health_attempts):
                live = self.health_probe("http://127.0.0.1:18080/health/live")
                ready = self.health_probe("http://127.0.0.1:18080/health/ready")
                if live and ready:
                    if prior:
                        self._write(self.root / "state/previous-image", prior + "\n")
                    candidate.replace(self.root / "current.env")
                    return
                if attempt + 1 < self.health_attempts:
                    time.sleep(2)
            raise RuntimeError("health check failed")
        except Exception:
            if prior:
                rollback_env = self.root / "current.env"
                for step, args in (
                    ("pull", ("pull", "app")),
                    ("up", ("up", "-d", "--no-deps", "app")),
                ):
                    try:
                        self.runner.run(self._compose(rollback_env, *args))
                    except Exception:
                        logger.error("deployment_rollback_failed", extra={"rollback_step": step})
            else:
                try:
                    self.runner.run(self._compose(candidate, "stop", "app"))
                except Exception:
                    logger.error("deployment_cleanup_failed", extra={"cleanup_step": "stop"})
            raise


def main(argv: list[str] | None = None) -> int:
    args = sys.argv if argv is None else argv
    if len(args) != 2:
        raise SystemExit(2)
    Deployer().deploy(args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
