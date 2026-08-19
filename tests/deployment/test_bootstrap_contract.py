from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_bootstrap_never_accepts_or_echoes_credentials() -> None:
    script = (ROOT / "deploy/bootstrap.sh").read_text(encoding="utf-8")
    assert "set -x" not in script
    assert "GHCR_TOKEN" not in script
    assert "read:packages" not in script
    assert 'if [ "$#" -ne 0 ]' in script


def test_bootstrap_generates_missing_secrets_via_atomic_temp_files() -> None:
    script = (ROOT / "deploy/bootstrap.sh").read_text(encoding="utf-8")
    assert 'mktemp "$ROOT/secrets/' in script
    assert 'mv "$temp" "$target"' in script
    assert 'chmod 0600 "$temp"' in script


def test_runbook_uses_password_stdin_and_digest_only_deploy() -> None:
    readme = (ROOT / "deploy/README.md").read_text(encoding="utf-8")
    assert "--password-stdin" in readme
    assert "read:packages" in readme
    assert "/opt/placegame-mcp/bin/deploy sha256:" in readme
    assert "write:packages" not in readme
