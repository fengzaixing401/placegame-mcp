# PlaceGame GHCR and OneSSH Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Publish one immutable linux/arm64 PlaceGame image to private GHCR and deploy its digest to the Singapore Docker host without changing unrelated workloads.

**Architecture:** Repository code supplies a migration-capable image, an isolated Compose project, and a digest-only deployment controller rooted at /opt/placegame-mcp. Separate GitHub workflows keep pull-request permissions read-only while release events publish the arm64 image. OneSSH performs only the final fixed-script invocation after an operator bootstrap installs runtime secrets and a read-only GHCR credential.

**Tech Stack:** Python 3.12, uv, FastAPI, Alembic, PostgreSQL 16, Docker Compose 2.28+, Docker Buildx/QEMU, GitHub Actions, GHCR, pytest, PyYAML, and OneSSH MCP.

**Implementation model:** gpt-5.6-terra at medium reasoning effort.

**Review model:** gpt-5.6-sol at max reasoning effort, read-only after each task and once for the complete branch.

## Global Constraints

- Publish exactly ghcr.io/fengzaixing401/placegame-mcp for linux/arm64.
- Pull-request validation has contents: read only and never pushes an image.
- The release workflow has contents: read and packages: write; main, v* tags, and workflow_dispatch publish.
- Release builds use Buildx with QEMU and record the immutable sha256 digest.
- The image contains /app/alembic.ini and /app/migrations and runs migrations with alembic upgrade head from /app.
- The Compose project name is placegame-mcp and its only services are app, migrate, and postgres.
- PostgreSQL publishes no host port; app publishes exactly 127.0.0.1:18080:8000.
- Secrets live only in root-owned files under /opt/placegame-mcp/secrets and enter containers through Docker secret files.
- App and migrate receive PLACEGAME_DATABASE_URL_FILE, PLACEGAME_MASTER_KEY_FILE, and PLACEGAME_MCP_TOKEN_FILE paths; postgres receives POSTGRES_PASSWORD_FILE.
- Routine deployment accepts exactly one digest matching sha256:[0-9a-f]{64} and accepts no tag, repository, path, command fragment, or secret.
- Deployment always uses /opt/placegame-mcp/deploy/compose.yaml, /opt/placegame-mcp as project directory, and placegame-mcp as project name.
- Pull or migration failure leaves a running app untouched. Health failure restores only the prior app image when one exists. Database downgrade is never automatic.
- Existing containers, networks, volumes, ports, and 1Panel/OpenResty configuration are not changed.
- The server GHCR credential is provisioned once with read:packages and is never committed, echoed, or passed as a deployment argument.
- Do not add WebUI, scheduler, RBAC, multi-architecture publishing, SBOM, vulnerability scanning, SSH-in-Actions, domain, or TLS work.

## File Map

- Modify Dockerfile: include migration assets while retaining the current Python-only runtime.
- Create .dockerignore: exclude local state, worktrees, caches, secrets, and test output.
- Create deploy/compose.yaml: isolated app, migrate, and PostgreSQL services.
- Create deploy/env.example: non-secret digest-pinned image state.
- Create tests/deployment/test_container_contract.py: image and Compose boundary tests.
- Create deploy/__init__.py: make deployment controller imports explicit.
- Create deploy/placegame_deploy.py: pure-stdlib digest validator and fixed Compose deployment controller.
- Create deploy/bin/deploy: fixed Python wrapper installed on the server.
- Create deploy/bootstrap.sh: idempotent root bootstrap for files and generated runtime secrets.
- Create deploy/README.md: credential bootstrap, deployment, recovery, and verification runbook.
- Create tests/deployment/test_deploy_controller.py: command ordering, failure, health, and rollback tests.
- Create tests/deployment/test_bootstrap_contract.py: bootstrap and credential-handling contract tests.
- Modify .github/workflows/build-image.yml: read-only PR/feature validation.
- Create .github/workflows/release-image.yml: arm64 GHCR publication and digest artifact.
- Create tests/deployment/test_workflows.py: trigger, permission, platform, and no-SSH workflow tests.

---

### Task 1: Build the Migration-Capable Image and Isolated Compose Stack

**Files:**
- Modify: Dockerfile
- Create: .dockerignore
- Create: deploy/compose.yaml
- Create: deploy/env.example
- Create: tests/deployment/test_container_contract.py

**Interfaces:**
- Consumes: placegame.app:create_app, Settings *_FILE readers, alembic.ini, and migrations/.
- Produces: one image usable by app and migrate plus a Compose file consumed by Task 2.

- [ ] **Step 1: Write the failing container contract tests**

Create tests/deployment/test_container_contract.py with these assertions:

~~~python
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]


def compose() -> dict:
    return yaml.safe_load((ROOT / "deploy/compose.yaml").read_text(encoding="utf-8"))


def test_image_contains_migration_runtime() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY alembic.ini ./" in dockerfile
    assert "COPY migrations ./migrations" in dockerfile
    assert 'WORKDIR /app' in dockerfile


def test_compose_has_only_placegame_services_and_ports() -> None:
    model = compose()
    services = model["services"]
    assert model["name"] == "placegame-mcp"
    assert set(services) == {"app", "migrate", "postgres"}
    assert services["app"]["ports"] == ["127.0.0.1:18080:8000"]
    assert "ports" not in services["postgres"]
    assert services["postgres"]["image"] == "postgres:16-alpine"
    assert "subboost" not in (ROOT / "deploy/compose.yaml").read_text().lower()


def test_compose_uses_secret_files_and_explicit_migration_command() -> None:
    services = compose()["services"]
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert services["migrate"]["working_dir"] == "/app"
    assert services["postgres"]["environment"] == {
        "POSTGRES_DB": "placegame",
        "POSTGRES_USER": "placegame",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres_password",
    }
    expected = {
        "PLACEGAME_DATABASE_URL_FILE": "/run/secrets/database_url",
        "PLACEGAME_MASTER_KEY_FILE": "/run/secrets/placegame_master_key",
        "PLACEGAME_MCP_TOKEN_FILE": "/run/secrets/placegame_mcp_token",
    }
    assert services["app"]["environment"] == expected
    assert services["migrate"]["environment"] == expected
~~~

- [ ] **Step 2: Run the focused test and confirm the expected failure**

Run:

~~~powershell
uv run pytest tests/deployment/test_container_contract.py -q
~~~

Expected: failure because deploy/compose.yaml does not exist and Dockerfile lacks migration COPY instructions.

- [ ] **Step 3: Implement the image and Compose model**

Retain the existing Python 3.12/uv Dockerfile flow and add:

~~~dockerfile
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
~~~

Create deploy/compose.yaml with the following fixed structure:

~~~yaml
name: placegame-mcp

x-placegame-app: &placegame-app
  image: ${PLACEGAME_IMAGE:?PLACEGAME_IMAGE is required}
  environment:
    PLACEGAME_DATABASE_URL_FILE: /run/secrets/database_url
    PLACEGAME_MASTER_KEY_FILE: /run/secrets/placegame_master_key
    PLACEGAME_MCP_TOKEN_FILE: /run/secrets/placegame_mcp_token
  secrets:
    - source: database_url
      target: database_url
    - source: master_key
      target: placegame_master_key
    - source: mcp_token
      target: placegame_mcp_token
  networks:
    - database
    - egress

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: placegame
      POSTGRES_USER: placegame
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
    secrets:
      - source: postgres_password
        target: postgres_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - database
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U placegame -d placegame"]
      interval: 5s
      timeout: 3s
      retries: 20

  migrate:
    <<: *placegame-app
    profiles: ["tools"]
    working_dir: /app
    command: ["alembic", "upgrade", "head"]
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"

  app:
    <<: *placegame-app
    restart: unless-stopped
    ports:
      - "127.0.0.1:18080:8000"
    depends_on:
      postgres:
        condition: service_healthy

networks:
  database:
    internal: true
  egress: {}

volumes:
  postgres_data: {}

secrets:
  database_url:
    file: /opt/placegame-mcp/secrets/database_url
  postgres_password:
    file: /opt/placegame-mcp/secrets/postgres_password
  master_key:
    file: /opt/placegame-mcp/secrets/master_key
  mcp_token:
    file: /opt/placegame-mcp/secrets/mcp_token
~~~

Create deploy/env.example containing only a non-secret example:

~~~dotenv
PLACEGAME_IMAGE=ghcr.io/fengzaixing401/placegame-mcp@sha256:0000000000000000000000000000000000000000000000000000000000000000
~~~

Create .dockerignore excluding .git, .worktrees, .superpowers, .venv, caches, coverage output, local .env files, deploy/current.env, deploy/state, deploy/secrets, and tests while retaining src, migrations, alembic.ini, pyproject.toml, and uv.lock.

- [ ] **Step 4: Run Task 1 verification**

Run:

~~~powershell
uv run pytest tests/deployment/test_container_contract.py -q
docker build -t placegame-mcp:deployment-contract .
docker run --rm placegame-mcp:deployment-contract alembic heads
~~~

Expected: tests pass, image builds, and Alembic prints 003_action_plan_execution_claim (head).

- [ ] **Step 5: Commit Task 1**

~~~powershell
git add Dockerfile .dockerignore deploy/compose.yaml deploy/env.example tests/deployment/test_container_contract.py
git commit -m "feat: add production deployment stack"
~~~

---

### Task 2: Implement Digest-Only Deployment and Bootstrap

**Files:**
- Create: deploy/__init__.py
- Create: deploy/placegame_deploy.py
- Create: deploy/bin/deploy
- Create: deploy/bootstrap.sh
- Create: deploy/README.md
- Create: tests/deployment/test_deploy_controller.py
- Create: tests/deployment/test_bootstrap_contract.py

**Interfaces:**
- Consumes: deploy/compose.yaml from Task 1 and the fixed server root /opt/placegame-mcp.
- Produces: validate_digest(value: str) -> str, ReleaseState, CommandRunner, Deployer.deploy(digest: str) -> None, and a one-argument CLI.

Use these frozen Python interfaces:

~~~python
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


IMAGE_REPOSITORY = "ghcr.io/fengzaixing401/placegame-mcp"
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
INSTALL_ROOT = Path("/opt/placegame-mcp")
PROJECT_NAME = "placegame-mcp"


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


class Deployer:
    def deploy(self, digest: str) -> None:
        raise NotImplementedError
~~~

- [ ] **Step 1: Write failing controller tests**

Cover all of the following in tests/deployment/test_deploy_controller.py:

~~~python
import pytest

from deploy.placegame_deploy import IMAGE_REPOSITORY, Deployer, validate_digest


@pytest.mark.parametrize(
    "value",
    [
        "latest",
        "sha256:ABCDEF",
        "sha256:" + "a" * 63,
        "sha256:" + "a" * 65,
        "sha256:" + "a" * 64 + ";docker ps",
    ],
)
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
        Deployer(runner=runner, root=tmp_path, health_probe=lambda _url: True).deploy(
            "sha256:" + "2" * 64
        )
    assert current.read_text(encoding="ascii").endswith("1" * 64 + "\n")
    assert not any(command[-4:] == ("up", "-d", "--no-deps", "app") for command in runner.commands)


def test_health_failure_restores_prior_app_only(tmp_path) -> None:
    write_current(tmp_path, IMAGE_REPOSITORY + "@sha256:" + "1" * 64)
    runner = RecordingRunner()
    with pytest.raises(RuntimeError, match="health"):
        Deployer(runner=runner, root=tmp_path, health_probe=lambda _url: False, health_attempts=2).deploy(
            "sha256:" + "2" * 64
        )
    assert (tmp_path / "current.env").read_text(encoding="ascii").endswith("1" * 64 + "\n")
    assert not any("down" in command for command in runner.commands)


def test_first_deploy_health_failure_stops_only_app(tmp_path) -> None:
    runner = RecordingRunner()
    with pytest.raises(RuntimeError, match="health"):
        Deployer(runner=runner, root=tmp_path, health_probe=lambda _url: False, health_attempts=1).deploy(
            "sha256:" + "2" * 64
        )
    assert any(command[-2:] == ("stop", "app") for command in runner.commands)
    assert not any("down" in command for command in runner.commands)
~~~

RecordingRunner raises only when every string in fail_when is present in an argv tuple and otherwise records argv unchanged.

- [ ] **Step 2: Run controller tests and confirm the expected import failure**

Run:

~~~powershell
uv run pytest tests/deployment/test_deploy_controller.py -q
~~~

Expected: collection fails because deploy.placegame_deploy does not exist.

- [ ] **Step 3: Implement the fixed deployment transaction**

Implement deploy/placegame_deploy.py with pure stdlib and shell=False subprocess calls. The deploy sequence is exact:

1. Validate the single digest and form IMAGE_REPOSITORY@digest.
2. Read prior /opt/placegame-mcp/current.env when present; it may contain only PLACEGAME_IMAGE=IMAGE_REPOSITORY@validated-digest.
3. Atomically write state/candidate.env with the candidate image.
4. Run fixed Compose pull app migrate using candidate.env.
5. Run fixed Compose up -d postgres using candidate.env.
6. Run fixed Compose --profile tools run --rm migrate using candidate.env.
7. Run fixed Compose up -d --no-deps app using candidate.env; current.env still identifies only the last healthy image.
8. Poll both http://127.0.0.1:18080/health/live and /health/ready for at most 30 attempts at two-second intervals.
9. After both probes succeed, write state/previous-image when a prior image exists and atomically promote candidate.env to current.env.
10. On health/up failure with a prior image, leave current.env unchanged, pull app with current.env, and run up -d --no-deps app. On first deployment, run stop app and leave current.env absent. Re-raise failure in both cases.

Compose argv must always begin with:

~~~python
(
    "docker",
    "compose",
    "--project-name",
    PROJECT_NAME,
    "--project-directory",
    str(root),
    "--env-file",
    str(env_file),
    "--file",
    str(root / "deploy" / "compose.yaml"),
)
~~~

The CLI accepts len(argv) == 2 only and prints no environment or secret data. deploy/bin/deploy contains:

~~~bash
#!/usr/bin/env bash
set -euo pipefail
exec python3 /opt/placegame-mcp/deploy/placegame_deploy.py "$@"
~~~

- [ ] **Step 4: Implement idempotent root bootstrap and runbook**

deploy/bootstrap.sh must:

- Require effective UID 0 and accept no arguments.
- Install compose.yaml, placegame_deploy.py, and bin/deploy beneath /opt/placegame-mcp with root ownership.
- Create secrets with mode 0700 and state with mode 0755.
- Create missing secrets with umask 077, without replacing existing values.
- Generate a URL-safe 32-byte PostgreSQL password, a 32-byte URL-safe base64 master key, and an exact 43-character token_urlsafe(32) MCP token.
- Write database_url as postgresql+asyncpg://placegame:PASSWORD@postgres:5432/placegame, substituting the newly generated URL-safe password before writing the file.
- Set all four secret files to mode 0600 and never use set -x.

deploy/README.md must document:

~~~bash
sudo bash deploy/bootstrap.sh
read -r -p "GHCR username: " GHCR_USERNAME
read -r -s -p "GHCR read token: " GHCR_TOKEN
printf '%s' "$GHCR_TOKEN" | sudo docker login ghcr.io --username "$GHCR_USERNAME" --password-stdin
unset GHCR_TOKEN
read -r -p "Published sha256 digest: " IMAGE_DIGEST
[[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 2
sudo /opt/placegame-mcp/bin/deploy "$IMAGE_DIGEST"
curl --fail http://127.0.0.1:18080/health/live
curl --fail http://127.0.0.1:18080/health/ready
~~~

State explicitly that the token needs read:packages only, database downgrade is manual, and the routine deploy command never accepts a token.

- [ ] **Step 5: Add bootstrap contract tests**

tests/deployment/test_bootstrap_contract.py reads bootstrap.sh and README.md and asserts:

~~~python
def test_bootstrap_never_accepts_or_echoes_credentials() -> None:
    script = (ROOT / "deploy/bootstrap.sh").read_text(encoding="utf-8")
    assert "set -x" not in script
    assert "GHCR_TOKEN" not in script
    assert "read:packages" not in script
    assert 'if [ "$#" -ne 0 ]' in script


def test_runbook_uses_password_stdin_and_digest_only_deploy() -> None:
    readme = (ROOT / "deploy/README.md").read_text(encoding="utf-8")
    assert "--password-stdin" in readme
    assert "read:packages" in readme
    assert "/opt/placegame-mcp/bin/deploy sha256:" in readme
    assert "write:packages" not in readme
~~~

- [ ] **Step 6: Run Task 2 verification**

Run:

~~~powershell
uv run pytest tests/deployment/test_deploy_controller.py tests/deployment/test_bootstrap_contract.py -q
uv run pyright deploy/placegame_deploy.py tests/deployment
~~~

Expected: all focused tests pass and Pyright reports zero errors.

- [ ] **Step 7: Commit Task 2**

~~~powershell
git add deploy/__init__.py deploy/placegame_deploy.py deploy/bin/deploy deploy/bootstrap.sh deploy/README.md tests/deployment/test_deploy_controller.py tests/deployment/test_bootstrap_contract.py
git commit -m "feat: add digest-only deployment controller"
~~~

---

### Task 3: Publish the ARM64 Image from GitHub Actions

**Files:**
- Modify: .github/workflows/build-image.yml
- Create: .github/workflows/release-image.yml
- Create: tests/deployment/test_workflows.py

**Interfaces:**
- Consumes: Task 1 Dockerfile.
- Produces: a private GHCR image digest and image-digest artifact used by the controller-owned OneSSH gate.

- [ ] **Step 1: Write failing workflow contract tests**

Use yaml.BaseLoader so the key on remains a string. Assert:

~~~python
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    return yaml.load(
        (WORKFLOWS / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def workflow_text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8").lower()


def test_validation_workflow_is_read_only_and_never_pushes() -> None:
    workflow = load("build-image.yml")
    assert workflow["permissions"] == {"contents": "read"}
    text = workflow_text("build-image.yml")
    assert "push: true" not in text
    assert "packages: write" not in text


def test_release_workflow_publishes_arm64_on_allowed_events() -> None:
    workflow = load("release-image.yml")
    assert workflow["permissions"] == {"contents": "read", "packages": "write"}
    triggers = workflow["on"]
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["push"]["tags"] == ["v*"]
    assert "workflow_dispatch" in triggers
    text = workflow_text("release-image.yml")
    assert "docker/setup-qemu-action@" in text
    assert "docker/setup-buildx-action@" in text
    assert "platforms: linux/arm64" in text
    assert "ghcr.io/fengzaixing401/placegame-mcp" in text
    assert "steps.build.outputs.digest" in text
    assert "ssh" not in text.lower()
~~~

- [ ] **Step 2: Run workflow tests and confirm the expected failure**

Run:

~~~powershell
uv run pytest tests/deployment/test_workflows.py -q
~~~

Expected: failure because release-image.yml does not exist and build-image.yml has no explicit permissions.

- [ ] **Step 3: Restrict the validation workflow**

Keep its existing image, health, unauthorized MCP, and authenticated tool-list smoke checks. Add:

~~~yaml
permissions:
  contents: read
~~~

Do not add login, package permissions, or push behavior.

- [ ] **Step 4: Add the release workflow**

Create release-image.yml with:

- on.push.branches [main], on.push.tags [v*], and workflow_dispatch.
- permissions contents: read and packages: write.
- checkout, setup-qemu, setup-buildx, login-action, metadata-action, build-push-action, and upload-artifact.
- registry ghcr.io, username github.actor, password secrets.GITHUB_TOKEN.
- build-push platforms linux/arm64 and push true.
- tags for main, the exact v* tag, sha-${{ github.sha }}, and manual-${{ github.run_number }}.
- an image-digest.txt artifact containing ghcr.io/fengzaixing401/placegame-mcp@ followed by steps.build.outputs.digest.
- a post-push linux/arm64 container health smoke using generated CI-only master key and MCP token.

No step may contain ssh, OneSSH, a server address, or a repository secret other than GITHUB_TOKEN.

- [ ] **Step 5: Run Task 3 verification**

Run:

~~~powershell
uv run pytest tests/deployment/test_workflows.py tests/deployment/test_container_contract.py -q
uv run python -c "import yaml; [yaml.safe_load(open(p, encoding='utf-8')) for p in ['.github/workflows/build-image.yml', '.github/workflows/release-image.yml']]"
~~~

Expected: workflow and container contracts pass and both workflow files parse.

- [ ] **Step 6: Commit Task 3**

~~~powershell
git add .github/workflows/build-image.yml .github/workflows/release-image.yml tests/deployment/test_workflows.py
git commit -m "ci: publish ARM64 image to GHCR"
~~~

---

### Task 4: Controller-Owned Verification and Singapore Deployment Gate

This task is not delegated to an implementation agent. The controller runs each gate once, records exact evidence, and does not repeat unchanged suites.

- [ ] **Step 1: Run the complete local gate**

~~~powershell
uv lock --check
uv run pytest -m "not integration" -q
uv run pyright src tests deploy/placegame_deploy.py
git diff --check
~~~

Expected: lock is current, tests pass with zero failures, Pyright reports zero errors, and diff check is clean.

- [ ] **Step 2: Request final Sol review**

Generate one review package from merge-base to HEAD. Sol reviews the complete deployment diff against the design and this plan and ends with Approved or one finite fix list. Terra handles any Critical/Important fixes, runs only covering tests, and Sol performs one focused re-review.

- [ ] **Step 3: Push, merge, and run the release workflow**

~~~powershell
git push origin feat/placegame-idle-v1
gh pr create --base main --head feat/placegame-idle-v1 --title "Deploy PlaceGame through GHCR and OneSSH" --body "Publishes an ARM64 GHCR image and adds the digest-only Singapore deployment stack."
gh pr merge --merge --delete-branch=false
$mainSha = gh api repos/fengzaixing401/placegame-mcp/commits/main --jq .sha
do {
  Start-Sleep -Seconds 3
  $releaseRunId = gh run list --workflow release-image.yml --commit $mainSha --limit 1 --json databaseId --jq '.[0].databaseId'
} until ($releaseRunId)
gh run watch $releaseRunId --exit-status
gh run download $releaseRunId --name image-digest --dir .artifacts/release
$imageRef = (Get-Content -Raw .artifacts/release/image-digest.txt).Trim()
if (-not [regex]::IsMatch($imageRef, '\Aghcr\.io/fengzaixing401/placegame-mcp@sha256:[0-9a-f]{64}\z')) {
  throw "release artifact did not contain the expected immutable image reference"
}
$validatedDigest = $imageRef.Split('@', 2)[1]
~~~

The release workflow must run from the merged main commit because a new workflow_dispatch file is not dispatchable until it exists on the default branch.

Validate image-digest.txt against:

~~~regex
\Aghcr\.io/fengzaixing401/placegame-mcp@sha256:[0-9a-f]{64}\z
~~~

- [ ] **Step 4: Satisfy the private GHCR credential prerequisite**

Use an operator-supplied credential with read:packages only. Feed it through docker login --password-stdin on 新加坡. Do not put the credential in a command argument, file under the repository, OneSSH argument, or captured log. If no such credential is available, stop before mutating /opt/placegame-mcp and report this single external prerequisite.

- [ ] **Step 5: Bootstrap and deploy through OneSSH**

Before host operations, call OneSSH hosts_list and memory_recall for the authorized host 新加坡. Record the IDs and running state of all non-PlaceGame containers, especially subboost-db-1.

On 新加坡:

~~~bash
cd /root/placegame-mcp-p1-run
git fetch origin main
git checkout main
git pull --ff-only origin main
sudo bash deploy/bootstrap.sh
sudo /opt/placegame-mcp/bin/deploy "$validated_digest"
curl --fail --silent http://127.0.0.1:18080/health/live
curl --fail --silent http://127.0.0.1:18080/health/ready
~~~

- [ ] **Step 6: Verify persistence and isolation once**

Record the current alembic_version value and PlaceGame PostgreSQL volume identity. Recreate only app with the fixed Compose project, then verify both health endpoints, the same alembic_version, and the same volume identity. Compare the earlier non-PlaceGame container IDs and states; they must be unchanged.

## Acceptance Criteria

- The published artifact resolves to a linux/arm64 image at the fixed GHCR repository and immutable digest.
- Pull-request validation cannot publish packages.
- The same image starts the app and runs Alembic migrations.
- The server stack uses its own PostgreSQL volume and exposes only loopback port 18080.
- Runtime secret values do not appear in repository files, workflow logs, Compose environment values, or deployment arguments.
- Invalid digest input performs no Docker command.
- Pull/migration failure leaves a running app untouched; health failure follows the documented app-only rollback behavior.
- Both health endpoints pass on 新加坡 after app recreation and database state persists.
- Existing non-PlaceGame workloads remain unchanged.
