import pytest

from deploy.placegame_deploy import IMAGE_REPOSITORY, Deployer, validate_digest


@pytest.mark.parametrize("value", ["latest", "sha256:ABCDEF", "sha256:" + "a" * 63, "sha256:" + "a" * 65, "sha256:" + "a" * 64 + ";docker ps"])
def test_validate_digest_rejects_non_exact_values(value: str) -> None:
    with pytest.raises(ValueError, match="invalid image digest"):
        validate_digest(value)


def test_validate_digest_accepts_exact_lowercase_digest() -> None:
    value = "sha256:" + "a" * 64
    assert validate_digest(value) == value


class RecordingRunner:
    def __init__(self, fail_when: tuple[str, ...] = ()) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.fail_when = fail_when

    def run(self, argv: tuple[str, ...]) -> None:
        self.commands.append(argv)
        if self.fail_when and all(part in argv for part in self.fail_when):
            raise RuntimeError("recorded command failure")


def write_current(root, image: str):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "current.env"
    path.write_text(f"PLACEGAME_IMAGE={image}\n", encoding="ascii")
    return path


def test_compose_argv_is_fixed_to_placegame_project(tmp_path) -> None:
    runner = RecordingRunner()
    deployer = Deployer(runner=runner, root=tmp_path, health_probe=lambda _url: True)
    deployer.deploy("sha256:" + "b" * 64)
    assert all(command[:3] == ("docker", "compose", "--project-name") for command in runner.commands)
    assert all("placegame-mcp" in command for command in runner.commands)
    assert all(str(tmp_path / "deploy/compose.yaml") in command for command in runner.commands)


def test_migration_failure_never_replaces_current_state_or_app(tmp_path) -> None:
    current = write_current(tmp_path, IMAGE_REPOSITORY + "@sha256:" + "1" * 64)
    runner = RecordingRunner(fail_when=("run", "--rm", "migrate"))
    with pytest.raises(RuntimeError):
        Deployer(runner=runner, root=tmp_path, health_probe=lambda _url: True).deploy("sha256:" + "2" * 64)
    assert current.read_text(encoding="ascii").endswith("1" * 64 + "\n")
    assert not any(command[-4:] == ("up", "-d", "--no-deps", "app") for command in runner.commands)


def test_health_failure_restores_prior_app_only(tmp_path) -> None:
    write_current(tmp_path, IMAGE_REPOSITORY + "@sha256:" + "1" * 64)
    runner = RecordingRunner()
    with pytest.raises(RuntimeError, match="health"):
        Deployer(runner=runner, root=tmp_path, health_probe=lambda _url: False, health_attempts=2).deploy("sha256:" + "2" * 64)
    assert (tmp_path / "current.env").read_text(encoding="ascii").endswith("1" * 64 + "\n")
    assert not any("down" in command for command in runner.commands)


def test_first_deploy_health_failure_stops_only_app(tmp_path) -> None:
    runner = RecordingRunner()
    with pytest.raises(RuntimeError, match="health"):
        Deployer(runner=runner, root=tmp_path, health_probe=lambda _url: False, health_attempts=1).deploy("sha256:" + "2" * 64)
    assert any(command[-2:] == ("stop", "app") for command in runner.commands)
    assert not any("down" in command for command in runner.commands)
