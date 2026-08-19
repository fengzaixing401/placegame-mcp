import re, subprocess, time, urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
IMAGE_REPOSITORY = "ghcr.io/fengzaixing401/placegame-mcp"
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
INSTALL_ROOT = Path("/opt/placegame-mcp")
PROJECT_NAME = "placegame-mcp"
def validate_digest(value: str) -> str:
    if DIGEST_PATTERN.fullmatch(value) is None: raise ValueError("invalid image digest")
    return value
@dataclass(frozen=True)
class ReleaseState: image: str
class CommandRunner(Protocol):
    def run(self, argv: tuple[str, ...]) -> None: raise NotImplementedError
class SubprocessRunner:
    def run(self, argv: tuple[str, ...]) -> None: subprocess.run(argv, check=True, shell=False)
class Deployer:
    def __init__(self, runner: CommandRunner | None = None, root: Path = INSTALL_ROOT, health_probe: Callable[[str], bool] | None = None, health_attempts: int = 30) -> None:
        self.runner, self.root, self.health_probe, self.health_attempts = runner or SubprocessRunner(), Path(root), health_probe or self._probe, health_attempts
    def _probe(self, url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=2) as response: return 200 <= response.status < 300
        except Exception: return False
    def _compose(self, env: Path, *args: str) -> tuple[str, ...]:
        return ("docker", "compose", "--project-name", PROJECT_NAME, "--project-directory", str(self.root), "--env-file", str(env), "--file", str(self.root / "deploy/compose.yaml"), *args)
    def _read_current(self) -> str | None:
        p = self.root / "current.env"
        if not p.exists(): return None
        image = p.read_text(encoding="ascii").strip().removeprefix("PLACEGAME_IMAGE=")
        if not image.startswith(IMAGE_REPOSITORY + "@"): raise ValueError("invalid current state")
        validate_digest(image.split("@", 1)[1]); return image
    def _write(self, p: Path, text: str) -> None:
        p.parent.mkdir(parents=True, exist_ok=True); t = p.with_name(p.name + ".tmp"); t.write_text(text, encoding="ascii"); t.replace(p)
    def deploy(self, digest: str) -> None:
        image = IMAGE_REPOSITORY + "@" + validate_digest(digest); prior = self._read_current(); candidate = self.root / "state/candidate.env"; self._write(candidate, f"PLACEGAME_IMAGE={image}\n")
        self.runner.run(self._compose(candidate, "pull", "app")); self.runner.run(self._compose(candidate, "up", "-d", "postgres")); self.runner.run(self._compose(candidate, "--profile", "tools", "run", "--rm", "migrate"))
        try:
            self.runner.run(self._compose(candidate, "up", "-d", "--no-deps", "app"))
            for i in range(self.health_attempts):
                if self.health_probe("http://127.0.0.1:18080/health/live") and self.health_probe("http://127.0.0.1:18080/health/ready"):
                    if prior: self._write(self.root / "state/previous-image", prior + "\n")
                    self._write(self.root / "current.env", f"PLACEGAME_IMAGE={image}\n"); return
                if i + 1 < self.health_attempts: time.sleep(2)
            raise RuntimeError("health check failed")
        except Exception:
            if prior:
                self.runner.run(self._compose(self.root / "current.env", "pull", "app")); self.runner.run(self._compose(self.root / "current.env", "up", "-d", "--no-deps", "app"))
            else: self.runner.run(self._compose(candidate, "stop", "app"))
            raise
def main(argv: list[str] | None = None) -> int:
    import sys
    args = sys.argv if argv is None else argv
    if len(args) != 2: raise SystemExit(2)
    Deployer().deploy(args[1]); return 0
if __name__ == "__main__": raise SystemExit(main())
