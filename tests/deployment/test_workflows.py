from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github/workflows"


def load(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_build_workflow_is_read_only_and_preserves_smoke_checks() -> None:
    model = load("build-image.yml")
    assert model["permissions"] == {"contents": "read"}
    source = text("build-image.yml")
    assert "docker build" in source
    assert "health" in source
    assert "unauthorized" in source
    assert "list_tools" in source
    assert "push: true" not in source
    assert "packages: write" not in source
    assert "docker login" not in source


def test_release_workflow_has_triggers_permissions_and_required_actions() -> None:
    model = load("release-image.yml")
    assert model["on"]["push"]["branches"] == ["main"]
    assert model["on"]["push"]["tags"] == ["v*"]
    assert "workflow_dispatch" in model["on"]
    assert model["permissions"] == {"contents": "read", "packages": "write"}
    source = text("release-image.yml")
    for action in ("setup-qemu", "setup-buildx", "login-action", "metadata-action", "build-push-action", "upload-artifact"):
        assert action in source
    assert "registry: ghcr.io" in source
    assert "username: ${{ github.actor }}" in source
    assert "password: ${{ secrets.GITHUB_TOKEN }}" in source
    assert "ghcr.io/fengzaixing401/placegame-mcp" in source
    assert "platforms: linux/arm64" in source
    assert "push: true" in source


def test_release_workflow_tags_digest_artifact_and_isolation() -> None:
    source = text("release-image.yml")
    for marker in ("type=raw,value=main", "type=ref,event=tag", "sha-${{ github.sha }}", "manual-${{ github.run_number }}"):
        assert marker in source
    assert "ghcr.io/fengzaixing401/placegame-mcp@${{ steps.build.outputs.digest }}" in source
    assert "image-digest.txt" in source
    assert "linux/arm64" in source
    assert "docker run --platform linux/arm64" in source
    assert "docker logs placegame-release-smoke" in source
    assert "docker inspect --format='status={{.State.Status}} health={{.State.Health.Status}}' placegame-release-smoke" in source
    assert "docker inspect placegame-release-smoke" not in source
    assert "ssh" not in source.lower()
    assert "onessh" not in source.lower()
    assert "/opt/" not in source
