# PlaceGame GitHub, GHCR, and OneSSH Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Publish a hardened AMD64/ARM64 PlaceGame image from a private GitHub repository and deploy an immutable digest to the Singapore ARM64 Docker host through OneSSH without disturbing its existing workloads.

**Architecture:** GitHub Actions validates the application, builds and scans one image per platform, then creates a private GHCR manifest with SBOM and provenance. A root-owned, digest-only deployment controller manages a loopback-bound app and dedicated PostgreSQL service; an authorized agent invokes it through OneSSH, while the existing 1Panel OpenResty edge remains unchanged.

**Tech Stack:** GitHub Actions, GitHub CLI, GHCR, Docker Buildx/QEMU, Trivy, OCI SBOM/provenance, Docker Compose 2.28+, Python 3.12, PostgreSQL 16, pytest, PyYAML, OneSSH MCP, Ubuntu ARM64, and 1Panel OpenResty.

## Global Constraints

- The GitHub repository is private and named fengzaixing401/placegame-mcp.
- The private image repository is ghcr.io/fengzaixing401/placegame-mcp.
- Every release manifest contains linux/amd64 and linux/arm64.
- Pull requests validate without packages:write and never push an image.
- main publishes main and sha-COMMIT; vMAJOR.MINOR.PATCH publishes MAJOR.MINOR.PATCH, MAJOR.MINOR, MAJOR, and latest.
- Deployment accepts only an OCI digest matching sha256 followed by exactly 64 lowercase hexadecimal characters.
- GitHub Actions never receives an SSH credential and never connects to the server.
- The server GHCR credential has read:packages only and is never committed, echoed, or passed as a deployment argument.
- The Singapore app publishes exactly 127.0.0.1:18080:8000; PostgreSQL publishes no host port.
- The PlaceGame stack uses its own postgres:16-alpine service, network, volume, user, database, and backup directory. It never reuses subboost-db.
- Existing 1Panel OpenResty keeps ports 80 and 443. This plan does not configure a domain or mutate 1Panel.
- The deployment script, Compose file, library, and secrets are root-owned and not writable by the routine OneSSH deployment identity.
- The routine OneSSH identity has no unrestricted Docker-group membership and can invoke only the fixed digest deployment command through sudo.
- Every schema migration is preceded by a compressed pg_dump when an initialized schema exists.
- Migrations use expand/contract compatibility; automatic rollback changes the app image only and never restores a database.
- A failed build, scan, pull, backup, migration, or health check never reports a successful deployment.
- Action references use full 40-character commit SHAs.
- Actionable HIGH or CRITICAL vulnerabilities block publication; unfixed upstream findings remain visible in reports.
- Both platform images receive an SPDX SBOM and build provenance tied to the source commit.
- Existing non-PlaceGame containers, networks, volumes, and published ports are never modified.

---

## Execution Order

Run the Repository Bootstrap Gate below immediately after this plan is approved. Then execute the application plans in this order:

1. PlaceGame MCP Core Tasks 1-8.
2. PlaceGame Inventory Tasks 1-6.
3. PlaceGame WebUI Tasks 1-8.
4. This deployment plan Tasks 1-6.

The controller records ledger entries with the plan prefix, for example Deployment Task 2, so task numbers from the four plans cannot be confused.

## Repository Bootstrap Gate

This is a controller-owned external-state gate, not a delegated implementation task.

- [ ] Verify the branch and expected absent remote.

Run:

~~~powershell
git status --short --branch
git remote -v
gh repo view fengzaixing401/placegame-mcp --json nameWithOwner,visibility,url
~~~

Expected before creation: the feature branch is clean, no origin remote exists, and gh reports that the repository was not found.

- [ ] Create the private repository without rewriting history and push both main and the feature branch.

Run from the primary checkout:

~~~powershell
gh repo create fengzaixing401/placegame-mcp --private --source . --remote origin --push
git push -u origin main
git -C .worktrees/placegame-automation push -u origin feat/placegame-automation
~~~

Expected: GitHub reports a private repository, origin uses the created URL, and both branches point at their existing local commits.

- [ ] Verify visibility and remote history.

Run:

~~~powershell
gh repo view fengzaixing401/placegame-mcp --json nameWithOwner,visibility,defaultBranchRef,url
git ls-remote --heads origin main feat/placegame-automation
~~~

Expected: visibility is PRIVATE, the default branch is main, and both remote heads resolve to their corresponding local commits.

## File Map

- Create: Dockerfile - final WebUI/Python image, non-root runtime, no browser packages.
- Create: .dockerignore - deterministic minimal build context.
- Consume: src/placegame/config.py - database URL secret-file reader created by Core Task 1.
- Create: deploy/__init__.py, deploy/placegame_deploy.py - digest validation and transactional deployment controller.
- Create: deploy/bin/deploy - fixed wrapper installed as /opt/placegame-mcp/bin/deploy.
- Create: deploy/compose.yaml - app, migrate, and dedicated PostgreSQL services.
- Create: deploy/env.example - non-secret digest-pinned release state.
- Create: deploy/bootstrap.sh - one-time root-owned directory, secret, and sudo setup.
- Create: deploy/README.md - operator prerequisites, secure GHCR login, 1Panel deferral, backup and recovery commands.
- Create: .github/workflows/ci.yml - read-only tests, scans, and platform build smoke checks.
- Create: .github/workflows/release.yml - per-platform digest builds, private-image scans, manifest publication, registry-native SBOM/provenance, and digest artifact.
- Create: .github/branch-protection.json - exact main protection payload.
- Create: tests/deployment/test_container_contract.py - Dockerfile, Compose, port, secret, and runtime policy tests.
- Create: tests/deployment/test_deploy_controller.py - digest, command construction, migration, health, and rollback tests.
- Create: tests/deployment/test_bootstrap_contract.py - root ownership, secret generation, and sudo boundary tests.
- Create: tests/deployment/test_workflows.py - workflow triggers, permissions, SHA pins, platforms, tags, scan, SBOM, and provenance tests.

## Frozen Deployment Interfaces

~~~python
# src/placegame/config.py
class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://placegame:placegame@postgres:5432/placegame"
    database_url_file: Path | None = Field(
        None,
        alias="PLACEGAME_DATABASE_URL_FILE",
    )

    def read_database_url(self) -> str:
        if self.database_url_file is None:
            return self.database_url
        value = self.database_url_file.read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError("database URL secret file is empty")
        return value
~~~

~~~python
# deploy/placegame_deploy.py
IMAGE_REPOSITORY = "ghcr.io/fengzaixing401/placegame-mcp"
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")

@dataclass(frozen=True)
class ReleaseState:
    image: str
    source_sha: str

class CommandRunner(Protocol):
    def run(
        self,
        argv: tuple[str, ...],
        *,
        capture: bool = False,
        input_bytes: bytes | None = None,
    ) -> subprocess.CompletedProcess: ...

class Deployer:
    def deploy(self, digest: str) -> None: ...
~~~

The production implementation uses subprocess with shell=False. No deployment code accepts a repository, tag, Compose path, command fragment, or arbitrary environment mapping from the caller.

### Task 1: Build the Production Image and Isolated Compose Stack

**Files:**
- Create: Dockerfile
- Create: .dockerignore
- Create: deploy/__init__.py
- Create: deploy/compose.yaml
- Create: deploy/env.example
- Create: tests/deployment/test_container_contract.py

**Interfaces:**
- Consumes: the completed WebUI build at web/dist, create_app, Alembic, and Settings.read_database_url() from Core Task 1.
- Produces: a multi-stage image containing /opt/placegame/deploy and a Compose project with app, migrate, and postgres.

- [ ] **Step 1: Write failing settings, image, and Compose contract tests**

~~~python
# tests/deployment/test_container_contract.py
from pathlib import Path

import yaml

from placegame.config import Settings

ROOT = Path(__file__).parents[2]


def test_database_url_is_read_from_secret_file(tmp_path):
    secret = tmp_path / "database_url"
    secret.write_text(
        "postgresql+asyncpg://placegame:secret@postgres:5432/placegame\n",
        encoding="utf-8",
    )
    settings = Settings(
        test_mode=True,
        game_base_url="http://127.0.0.1:9000",
        database_url_file=secret,
    )
    assert settings.read_database_url().endswith("@postgres:5432/placegame")


def test_compose_is_loopback_only_and_uses_dedicated_postgres():
    compose = yaml.safe_load((ROOT / "deploy/compose.yaml").read_text())
    services = compose["services"]
    assert set(services) == {"app", "migrate", "postgres"}
    assert services["app"]["ports"] == ["127.0.0.1:18080:8000"]
    assert "ports" not in services["postgres"]
    assert services["postgres"]["image"] == "postgres:16-alpine"
    assert services["app"]["image"] == "${PLACEGAME_IMAGE:?PLACEGAME_IMAGE is required}"
    assert services["migrate"]["image"] == services["app"]["image"]
    assert set(services["postgres"]["networks"]) == {"database"}
    assert set(services["app"]["networks"]) == {"database", "egress"}
    assert compose["networks"]["database"]["internal"] is True


def test_image_is_non_root_and_contains_no_browser_runtime():
    dockerfile = (ROOT / "Dockerfile").read_text()
    lowered = dockerfile.lower()
    assert "user 10001:10001" in lowered
    assert "playwright" not in lowered
    assert "chromium" not in lowered
    assert "copy deploy /opt/placegame/deploy" in lowered
    assert "from --platform=$buildplatform node:22-alpine as web-build" in lowered
~~~

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run: uv run pytest tests/deployment/test_container_contract.py -q

Expected: FAIL because Dockerfile and deploy/compose.yaml do not exist; the Core-provided Settings test already passes.

- [ ] **Step 3: Implement secret-file settings, Dockerfile, and Compose**

Retain the frozen Settings fields and read_database_url method from Core Task 1. Every database engine factory already calls settings.read_database_url() and never logs its value.

Create this Dockerfile structure:

~~~dockerfile
# syntax=docker/dockerfile:1.7
FROM --platform=$BUILDPLATFORM node:22-alpine AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.8.13 AS uv-bin

FROM python:3.12-slim AS runtime
ARG VCS_REF=unknown
RUN groupadd --gid 10001 placegame \
    && useradd --uid 10001 --gid 10001 --create-home placegame
WORKDIR /app
COPY --from=uv-bin /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY --from=web-build /build/web/dist ./web/dist
COPY deploy /opt/placegame/deploy
RUN chown -R 10001:10001 /app \
    && chmod -R go-w /opt/placegame/deploy
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONDONTWRITEBYTECODE=1
LABEL org.opencontainers.image.source="https://github.com/fengzaixing401/placegame-mcp" \
      org.opencontainers.image.revision="$VCS_REF"
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "placegame.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
~~~

Create deploy/compose.yaml with these exact boundaries:

~~~yaml
name: placegame-mcp

x-app: &app
  image: ${PLACEGAME_IMAGE:?PLACEGAME_IMAGE is required}
  pull_policy: always
  environment:
    PLACEGAME_DATABASE_URL_FILE: /run/secrets/database_url
    PLACEGAME_MASTER_KEY_FILE: /run/secrets/master_key
  secrets:
    - database_url
    - master_key
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
      - postgres_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - database
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U placegame -d placegame"]
      interval: 10s
      timeout: 5s
      retries: 12

  migrate:
    <<: *app
    profiles: ["tools"]
    command: ["alembic", "upgrade", "head"]
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"

  app:
    <<: *app
    restart: unless-stopped
    ports:
      - "127.0.0.1:18080:8000"
    depends_on:
      postgres:
        condition: service_healthy
    read_only: true
    tmpfs:
      - /tmp:size=64m,mode=1777
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"
      interval: 15s
      timeout: 5s
      retries: 8

networks:
  database:
    internal: true
  egress: {}

volumes:
  postgres_data: {}

secrets:
  database_url:
    file: ./secrets/database_url
  master_key:
    file: ./secrets/master_key
  postgres_password:
    file: ./secrets/postgres_password
~~~

deploy/env.example contains only:

~~~dotenv
PLACEGAME_IMAGE=ghcr.io/fengzaixing401/placegame-mcp@sha256:0000000000000000000000000000000000000000000000000000000000000000
PLACEGAME_SOURCE_SHA=0000000000000000000000000000000000000000
~~~

.dockerignore excludes .git, .worktrees, .superpowers, Python/Node caches, local environment files, secrets, PostgreSQL data, test reports, and web/node_modules while retaining source, migrations, deploy assets, and web/package-lock.json.

- [ ] **Step 4: Run container contract and application checks**

Run: uv run pytest tests/deployment/test_container_contract.py -q && uv run pyright src tests

Expected: container contract tests pass and Pyright reports zero errors.

- [ ] **Step 5: Commit the image and Compose checkpoint**

~~~bash
git add Dockerfile .dockerignore deploy/__init__.py deploy/compose.yaml deploy/env.example tests/deployment/test_container_contract.py
git commit -m "feat: add production image and isolated compose stack"
~~~

### Task 2: Implement the Digest-Only Transactional Deployer

**Files:**
- Create: deploy/placegame_deploy.py
- Create: deploy/bin/deploy
- Create: tests/deployment/test_deploy_controller.py

**Interfaces:**
- Consumes: deploy/compose.yaml, /opt/placegame-mcp/current.env, root-owned secrets, Docker, Compose, and the app readiness endpoints.
- Produces: validate_digest(), ReleaseState, CommandRunner, SubprocessRunner, Deployer.deploy(), and the fixed /opt/placegame-mcp/bin/deploy command.

- [ ] **Step 1: Write failing digest, mutation-order, and rollback tests**

~~~python
# tests/deployment/test_deploy_controller.py
import subprocess

import pytest

from deploy.placegame_deploy import Deployer, InvalidDigest, validate_digest


class FakeRunner:
    def __init__(self):
        self.commands = []

    def run(self, argv, *, capture=False, input_bytes=None):
        self.commands.append(tuple(argv))
        command = tuple(argv)
        if command[:3] == ("docker", "manifest", "inspect"):
            body = b'[{"Descriptor":{"platform":{"os":"linux","architecture":"arm64"}}}]'
            return subprocess.CompletedProcess(command, 0, body, b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")


def test_digest_rejects_tags_and_uppercase():
    with pytest.raises(InvalidDigest):
        validate_digest("latest")
    with pytest.raises(InvalidDigest):
        validate_digest("sha256:" + "A" * 64)


def test_migration_runs_after_backup_and_before_switch(tmp_path):
    runner = FakeRunner()
    events = []
    (tmp_path / "current.env").write_text(
        "PLACEGAME_IMAGE=ghcr.io/fengzaixing401/placegame-mcp@sha256:"
        + "1" * 64
        + "\nPLACEGAME_SOURCE_SHA="
        + "a" * 40
        + "\n"
    )
    deployer = Deployer(
        root=tmp_path,
        runner=runner,
        health_check=lambda: None,
        event_sink=events.append,
        preflight=lambda: None,
    )
    deployer.deploy("sha256:" + "2" * 64)
    assert events.index("backup") < events.index("migrate") < events.index("switch")


def test_unhealthy_candidate_restores_previous_digest(tmp_path):
    runner = FakeRunner()
    events = []
    previous = "sha256:" + "1" * 64
    (tmp_path / "current.env").write_text(
        "PLACEGAME_IMAGE=ghcr.io/fengzaixing401/placegame-mcp@"
        + previous
        + "\nPLACEGAME_SOURCE_SHA="
        + "a" * 40
        + "\n"
    )

    def unhealthy():
        raise RuntimeError("not ready")

    deployer = Deployer(
        root=tmp_path,
        runner=runner,
        health_check=unhealthy,
        event_sink=events.append,
        preflight=lambda: None,
    )
    with pytest.raises(RuntimeError, match="not ready"):
        deployer.deploy("sha256:" + "2" * 64)
    assert previous in (tmp_path / "current.env").read_text()
    assert "rollback" in events
~~~

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: uv run pytest tests/deployment/test_deploy_controller.py -q

Expected: FAIL because deploy.placegame_deploy does not exist.

- [ ] **Step 3: Implement the controller with structured subprocess calls**

Use this exact validation and state model:

~~~python
# deploy/placegame_deploy.py
from __future__ import annotations

import fcntl
import gzip
import json
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

IMAGE_REPOSITORY = "ghcr.io/fengzaixing401/placegame-mcp"
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class InvalidDigest(ValueError):
    pass


def validate_digest(value: str) -> str:
    if DIGEST_PATTERN.fullmatch(value) is None:
        raise InvalidDigest("deployment requires a lowercase sha256 OCI digest")
    return value


@dataclass(frozen=True)
class ReleaseState:
    image: str
    source_sha: str


class CommandRunner(Protocol):
    def run(self, argv, *, capture=False, input_bytes=None):
        return subprocess.CompletedProcess(argv, 0)


class SubprocessRunner:
    def run(self, argv, *, capture=False, input_bytes=None):
        return subprocess.run(
            tuple(argv),
            check=True,
            shell=False,
            input=input_bytes,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )


def default_health_check() -> None:
    for path in ("/health/live", "/health/ready"):
        with urllib.request.urlopen(
            "http://127.0.0.1:18080" + path,
            timeout=5,
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"health check failed for {path}")
~~~

Deployer uses constants rooted at /opt/placegame-mcp in production. Its constructor accepts general command-runner, health-check, event-sink, and preflight dependencies; production supplies secure defaults and tests supply deterministic implementations. Its public CLI accepts exactly one digest argument and exposes no test-only methods.

Implement these private operations in this exact order:

1. lock /run/lock/placegame-mcp-deploy.lock with fcntl.LOCK_EX | LOCK_NB;
2. require root ownership and non-group/world-writable modes for bin/deploy, lib/placegame_deploy.py, compose.yaml, and secrets;
3. require at least 5 GiB free under /opt/placegame-mcp;
4. run docker info and docker compose version;
5. run docker manifest inspect --verbose IMAGE_REPOSITORY@DIGEST with capture=True, parse JSON, and require a linux/arm64 descriptor;
6. run docker pull IMAGE_REPOSITORY@DIGEST;
7. inspect the pulled image label org.opencontainers.image.revision and require a 40-character lowercase source SHA;
8. write candidate.env atomically with PLACEGAME_IMAGE and that source SHA;
9. run docker compose --env-file candidate.env up -d postgres and wait for its healthy state;
10. if public.alembic_version exists, capture docker compose exec -T postgres pg_dump -U placegame placegame and write it through gzip.open to backups/TIMESTAMP.sql.gz with mode 0600;
11. run docker compose --env-file candidate.env --profile tools run --rm migrate;
12. copy current.env to previous.env when current.env exists, then atomically replace current.env with candidate.env;
13. run docker compose --env-file current.env up -d --no-deps app;
14. poll both health endpoints for at most 90 seconds;
15. append a mode-0600 JSON release record containing source SHA, digest, migration revision, timestamps, and outcome;
16. on a post-switch failure with previous.env, atomically restore it to current.env, recreate app, verify health, record rollback, and re-raise the original failure; on a failed first deployment with no previous.env, stop only the candidate app, record the failure, and re-raise.

Never restore a database automatically. A failure before the atomic current.env switch leaves the running app unchanged.

Create deploy/bin/deploy as this root-owned wrapper:

~~~bash
#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo "usage: deploy sha256:DIGEST" >&2
  exit 2
fi
exec /usr/bin/python3 /opt/placegame-mcp/lib/placegame_deploy.py "$1"
~~~

- [ ] **Step 4: Run controller tests and static checks**

Run: uv run pytest tests/deployment/test_deploy_controller.py -q && uv run pyright deploy tests/deployment

Expected: all digest, ordering, failure, and rollback tests pass; Pyright reports zero errors.

- [ ] **Step 5: Commit the deployer checkpoint**

~~~bash
git add deploy/placegame_deploy.py deploy/bin/deploy tests/deployment/test_deploy_controller.py
git commit -m "feat: add digest-only transactional deployer"
~~~

### Task 3: Add Read-Only Pull Request CI and Platform Smoke Builds

**Files:**
- Create: .github/workflows/ci.yml
- Create: .github/branch-protection.json
- Create: tests/deployment/test_workflows.py

**Interfaces:**
- Consumes: uv.lock, web/package-lock.json, Dockerfile, deployment tests, Testcontainers, and both health endpoints.
- Produces: the required CI check named required and an immutable branch-protection payload.

- [ ] **Step 1: Write failing workflow policy tests**

~~~python
# tests/deployment/test_workflows.py
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
SHA_PIN = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(),
        Loader=yaml.BaseLoader,
    )


def action_refs(document):
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step:
                yield step["uses"]


def test_ci_is_read_only_and_never_pushes():
    ci = workflow("ci.yml")
    assert ci["permissions"] == {"contents": "read"}
    assert "pull_request" in ci["on"]
    assert ci["jobs"]["image"]["strategy"]["matrix"]["platform"] == [
        "linux/amd64",
        "linux/arm64",
    ]
    assert all(SHA_PIN.fullmatch(ref) for ref in action_refs(ci))
    assert "push: true" not in (ROOT / ".github/workflows/ci.yml").read_text()
    assert ci["jobs"]["required"]["needs"] == ["python", "web", "image"]


def test_branch_protection_requires_ci_and_blocks_force_push():
    policy = json.loads(
        (ROOT / ".github/branch-protection.json").read_text()
    )
    assert policy["required_status_checks"]["strict"] is True
    assert policy["required_status_checks"]["contexts"] == ["required"]
    assert policy["allow_force_pushes"] is False
    assert policy["allow_deletions"] is False
~~~

- [ ] **Step 2: Run workflow tests and verify they fail**

Run: uv run pytest tests/deployment/test_workflows.py -q

Expected: FAIL because ci.yml and branch-protection.json do not exist.

- [ ] **Step 3: Implement CI with immutable action pins**

Use these verified action pins:

- actions/checkout at 11d5960a326750d5838078e36cf38b85af677262
- actions/setup-python at a26af69be951a213d495a4c3e4e4022e16d87065
- actions/setup-node at 49933ea5288caeca8642d1e84afbd3f7d6820020
- astral-sh/setup-uv at d0cc045d04ccac9d8b7881df0226f9e82c39688e
- docker/setup-qemu-action at c7c53464625b32c7a7e944ae62b3e17d2b600130
- docker/setup-buildx-action at 8d2750c68a42422c14e847fe6c8ac0403b4cbd6f
- docker/build-push-action at 10e90e3645eae34f1e60eeb005ba3a3d33f178e8
- aquasecurity/trivy-action at d2a0b60797ff03db6132bd4e2b293f9b37081297

ci.yml has top-level permissions contents: read and these jobs:

~~~yaml
name: CI

on:
  pull_request:
  push:
    branches-ignore: [main]

permissions:
  contents: read

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  python:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
        with:
          python-version: "3.12"
      - uses: astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e
        with:
          version: "0.8.13"
      - run: uv sync --frozen --all-groups
      - run: uv run pytest -q
      - run: uv run pyright src tests deploy
      - run: PLACEGAME_IMAGE=ghcr.io/fengzaixing401/placegame-mcp@sha256:0000000000000000000000000000000000000000000000000000000000000000 docker compose -f deploy/compose.yaml config --quiet
      - uses: aquasecurity/trivy-action@d2a0b60797ff03db6132bd4e2b293f9b37081297
        with:
          scan-type: fs
          scan-ref: .
          severity: HIGH,CRITICAL
          ignore-unfixed: true
          exit-code: "1"

  web:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
      - uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: web/package-lock.json
      - run: npm --prefix web ci
      - run: npm --prefix web run typecheck
      - run: npm --prefix web test -- --run
      - run: npm --prefix web run build

  image:
    runs-on: ubuntu-24.04
    strategy:
      fail-fast: false
      matrix:
        platform: [linux/amd64, linux/arm64]
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
      - uses: docker/setup-qemu-action@c7c53464625b32c7a7e944ae62b3e17d2b600130
      - uses: docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f
      - uses: docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8
        with:
          context: .
          platforms: ${{ matrix.platform }}
          load: true
          push: false
          tags: placegame-ci:smoke
          cache-from: type=gha,scope=ci-${{ matrix.platform }}
          cache-to: type=gha,mode=max,scope=ci-${{ matrix.platform }}
      - run: docker run --rm --platform "${{ matrix.platform }}" placegame-ci:smoke python -c "import placegame"

  required:
    if: always()
    runs-on: ubuntu-24.04
    needs: [python, web, image]
    steps:
      - run: test "${{ needs.python.result }}" = success
      - run: test "${{ needs.web.result }}" = success
      - run: test "${{ needs.image.result }}" = success
~~~

Create branch-protection.json:

~~~json
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["required"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": true
}
~~~

- [ ] **Step 4: Validate workflow policy and YAML**

Run: uv run pytest tests/deployment/test_workflows.py -q && git diff --check

Expected: workflow tests pass, every action reference is a 40-character SHA, and no whitespace error is reported.

- [ ] **Step 5: Commit the CI checkpoint**

~~~bash
git add .github/workflows/ci.yml .github/branch-protection.json tests/deployment/test_workflows.py
git commit -m "ci: validate application and platform images"
~~~

### Task 4: Publish Scanned Multi-Platform Releases to Private GHCR

**Files:**
- Create: .github/workflows/release.yml
- Modify: tests/deployment/test_workflows.py

**Interfaces:**
- Consumes: successful application checks, Dockerfile, GITHUB_TOKEN, main or vMAJOR.MINOR.PATCH refs.
- Produces: scanned per-platform image digests, one multi-platform manifest, tag set, registry-native SBOM/provenance, and release-digest artifact.

- [ ] **Step 1: Add failing release-policy tests**

~~~python
# append to tests/deployment/test_workflows.py
def test_release_has_least_privilege_and_exact_triggers():
    release = workflow("release.yml")
    assert release["permissions"] == {
        "contents": "read",
        "packages": "write",
    }
    assert release["on"]["push"]["branches"] == ["main"]
    assert release["on"]["push"]["tags"] == ["v*"]
    assert all(SHA_PIN.fullmatch(ref) for ref in action_refs(release))


def test_release_scans_platform_digests_before_manifest():
    text = (ROOT / ".github/workflows/release.yml").read_text()
    assert "linux/amd64" in text and "linux/arm64" in text
    assert "steps.platform.outputs.arch" in text
    assert "steps.build.outputs.digest" in text
    assert "ignore-unfixed: true" in text
    assert "severity: HIGH,CRITICAL" in text
    assert "TRIVY_USERNAME" in text and "TRIVY_PASSWORD" in text
    assert "sbom: true" in text
    assert "provenance: mode=max" in text
    assert "docker buildx imagetools create" in text
    assert "release-digest.txt" in text
~~~

- [ ] **Step 2: Run the release tests and verify they fail**

Run: uv run pytest tests/deployment/test_workflows.py::test_release_has_least_privilege_and_exact_triggers tests/deployment/test_workflows.py::test_release_scans_platform_digests_before_manifest -q

Expected: FAIL because release.yml does not exist.

- [ ] **Step 3: Implement digest build, scan, and manifest jobs**

release.yml uses the same immutable checkout, Python, Node, uv, QEMU, Buildx, build-push, and Trivy pins as CI plus:

- docker/login-action at c94ce9fb468520275223c153574b00df6fe4bcc9
- actions/upload-artifact at ea165f8d65b6e75b540449e92b4886f43607fa02
- actions/download-artifact at 634f93cb2916e3fdff6788551b99b062d0335ce0

The workflow has exact top-level controls:

~~~yaml
name: Release

on:
  push:
    branches: [main]
    tags: ["v*"]

permissions:
  contents: read
  packages: write

concurrency:
  group: release
  cancel-in-progress: false
~~~

The verify job repeats uv sync --frozen --all-groups, pytest, Pyright, npm ci, frontend type/test/build, and Compose config. Publication cannot depend on a previous workflow run.

The platform job is a matrix over linux/amd64 and linux/arm64. A shell step with id platform writes `arch=${PLATFORM#linux/}` to GITHUB_OUTPUT from a step-local PLATFORM environment variable. Every later architecture reference uses `${{ steps.platform.outputs.arch }}`; the workflow never assumes an `env.ARCH` value. The job logs in with github.actor and secrets.GITHUB_TOKEN, then uses docker/build-push-action with id build and these publication settings:

~~~yaml
build-args: VCS_REF=${{ github.sha }}
platforms: ${{ matrix.platform }}
outputs: type=image,name=ghcr.io/fengzaixing401/placegame-mcp,push-by-digest=true,name-canonical=true,push=true
sbom: true
provenance: mode=max
~~~

Immediately after each push, Trivy scans `ghcr.io/fengzaixing401/placegame-mcp@${{ steps.build.outputs.digest }}` with scan-type image, ignore-unfixed true, severity HIGH,CRITICAL, and exit-code 1. Supply private-registry authentication only through step environment values `TRIVY_USERNAME: ${{ github.actor }}` and `TRIVY_PASSWORD: ${{ secrets.GITHUB_TOKEN }}`. After the scan passes, write the digest to a file named for `${{ steps.platform.outputs.arch }}` and upload artifact `platform-digest-${{ steps.platform.outputs.arch }}`. The manifest job needs verify and the complete successful platform matrix, downloads both digest artifacts, validates each against `^sha256:[0-9a-f]{64}$`, and never accepts a tag as a source.

For main, the manifest job creates tags main and sha-${{ github.sha }}. For a version ref, a strict regular expression extracts MAJOR, MINOR, and PATCH and creates MAJOR.MINOR.PATCH, MAJOR.MINOR, MAJOR, and latest. Reject a version ref that does not match before running imagetools.

Create one manifest from the two candidate references using:

~~~bash
docker buildx imagetools create \
  --tag "ghcr.io/fengzaixing401/placegame-mcp:$TAG_ONE" \
  --tag "ghcr.io/fengzaixing401/placegame-mcp:$TAG_TWO" \
  "ghcr.io/fengzaixing401/placegame-mcp@$AMD64_DIGEST" \
  "ghcr.io/fengzaixing401/placegame-mcp@$ARM64_DIGEST"
~~~

Add all additional version tags with explicit --tag arguments in the same invocation. Give the create-and-inspect step id manifest; inspect the canonical tag with `docker buildx imagetools inspect`, require linux/amd64 and linux/arm64 descriptors plus BuildKit SBOM/provenance descriptors, and write the resulting index digest to its `digest` step output. Write that digest and the source SHA to release-digest.txt, append them to GITHUB_STEP_SUMMARY, and upload release-digest.txt as artifact release-digest-${{ github.sha }}. GitHub's separate artifact-attestation action is intentionally not used because private repositories under personal accounts may require GitHub Enterprise Cloud; BuildKit stores the required attestations with the private image in GHCR without granting unused id-token or attestations permissions.

- [ ] **Step 4: Run workflow tests and action syntax checks**

Run: uv run pytest tests/deployment/test_workflows.py -q

Expected: all CI and release policy tests pass, including exact triggers, least-privilege permissions, SHA pins, platforms, scans, registry-native SBOM/provenance, tags, and digest artifact.

- [ ] **Step 5: Commit the release checkpoint**

~~~bash
git add .github/workflows/release.yml tests/deployment/test_workflows.py
git commit -m "ci: publish scanned multi-platform ghcr releases"
~~~

### Task 5: Add Secure Server Bootstrap Assets

**Files:**
- Create: deploy/bootstrap.sh
- Create: deploy/README.md
- Create: tests/deployment/test_bootstrap_contract.py

**Interfaces:**
- Consumes: a verified release digest, root access for one-time setup, a preinstalled root GHCR read credential, and the assets embedded under /opt/placegame/deploy in the image.
- Produces: root-owned /opt/placegame-mcp, generated secrets, the fixed placegame-deploy identity, restricted sudo rule, and the installed fixed deployer.

- [ ] **Step 1: Write failing bootstrap security tests**

~~~python
# tests/deployment/test_bootstrap_contract.py
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_bootstrap_never_echoes_secrets_and_installs_root_owned_code():
    script = (ROOT / "deploy/bootstrap.sh").read_text()
    assert "set -euo pipefail" in script
    assert "umask 077" in script
    assert "openssl rand -base64 32" in script
    assert "openssl rand -hex 32" in script
    assert "install -o root -g root -m 0755" in script
    assert "install -o root -g root -m 0644" in script
    assert "placegame-deploy" in script
    assert "NOPASSWD: /opt/placegame-mcp/bin/deploy sha256:" in script
    assert "PLACEGAME_IMAGE=" not in script
    assert "set -x" not in script
    assert "cat /opt/placegame-mcp/secrets" not in script


def test_operator_guide_defers_domain_and_forbids_docker_group():
    guide = (ROOT / "deploy/README.md").read_text()
    assert "read:packages" in guide
    assert "do not paste the token into an agent message" in guide.lower()
    assert "127.0.0.1:18080" in guide
    assert "Do not add the deployment identity to the docker group" in guide
    assert "1Panel" in guide and "deferred" in guide
~~~

- [ ] **Step 2: Run bootstrap tests and verify they fail**

Run: uv run pytest tests/deployment/test_bootstrap_contract.py -q

Expected: FAIL because bootstrap.sh and deploy/README.md do not exist.

- [ ] **Step 3: Implement one-time root bootstrap**

deploy/bootstrap.sh accepts exactly one argument: a validated digest. It exits if not root, if the digest is malformed, or if /opt/placegame-mcp contains an unrecognized existing installation. The deployment username is the constant `placegame-deploy`; the script creates that locked-password, unprivileged user with a home directory and `/bin/bash` when absent, and rejects an existing identity with unexpected UID 0 or Docker-group membership. The operator installs the OneSSH public key for this account through the privileged bootstrap channel and then creates a separate routine OneSSH host entry whose SSH username is exactly `placegame-deploy`.

It performs these exact actions with set -euo pipefail and umask 077:

1. docker pull ghcr.io/fengzaixing401/placegame-mcp@DIGEST using the root credential installed by the operator;
2. docker create the image, docker cp /opt/placegame/deploy to a new mktemp directory, and remove the temporary container through a trap;
3. install directories bin, lib, releases, backups, and secrets as root:root with modes 0755, 0755, 0750, 0700, and 0700;
4. install compose.yaml and placegame_deploy.py as root:root 0644 and bin/deploy as root:root 0755;
5. generate secrets/master_key with openssl rand -base64 32 and secrets/postgres_password with openssl rand -hex 32 when and only when each file is absent;
6. build secrets/database_url from the hexadecimal password without printing it;
7. set all secret files to root:root 0600;
8. leave current.env and previous.env absent so the fixed deployer owns the first atomic release-state transition;
9. install /etc/sudoers.d/placegame-mcp-deploy as root:root 0440, granting only /opt/placegame-mcp/bin/deploy sha256:* to placegame-deploy;
10. validate the sudoers file with visudo -cf;
11. run the fixed deployer for the first release; and
12. print only the digest, service state, and loopback health result.

The script never runs docker login. deploy/README.md requires the operator to install a classic GitHub token with read:packages through sudo docker login ghcr.io --username fengzaixing401 --password-stdin in a private terminal or OneSSH secret prompt. It explicitly forbids putting that token in GitHub repository secrets, Compose, agent messages, command arguments, or shell history.

The guide documents key-only SSH setup for placegame-deploy, the dedicated routine OneSSH host entry, pre-migration backups, manual database restore requiring explicit operator approval, log locations, release records, the digest-only routine command, and later 1Panel requirements for MCP/SSE streaming. It states that domain configuration is deferred.

- [ ] **Step 4: Run bootstrap and deployment contract tests**

Run: uv run pytest tests/deployment/test_bootstrap_contract.py tests/deployment/test_deploy_controller.py tests/deployment/test_container_contract.py -q

Expected: all bootstrap, secret, ownership, digest, Compose, and rollback contract tests pass.

- [ ] **Step 5: Commit the bootstrap checkpoint**

~~~bash
git add deploy/bootstrap.sh deploy/README.md tests/deployment/test_bootstrap_contract.py
git commit -m "feat: add secure onessh deployment bootstrap"
~~~

### Task 6: Provision GitHub Policy, Publish, Deploy, and Prove Rollback

**Files:**
- Modify only if verification exposes a tested defect in Tasks 1-5.
- Verify: .github/branch-protection.json
- Verify: .github/workflows/ci.yml
- Verify: .github/workflows/release.yml
- Verify: deploy/compose.yaml
- Verify: deploy/bin/deploy
- Verify: deploy/bootstrap.sh

**Interfaces:**
- Consumes: GitHub origin, GitHub Actions, private GHCR, operator-installed root GHCR read credential, configured OneSSH MCP, and the Singapore host.
- Produces: protected main, private multi-platform image, first healthy ARM64 deployment, durable PostgreSQL state, and either a verified previous-digest rollback or an explicit pending rollback gate until a second legitimate release exists.

- [ ] **Step 1: Run all available local acceptance checks before external mutation**

Run:

~~~bash
uv run pytest -q
uv run pyright src tests deploy
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web test -- --run
npm --prefix web run build
git diff --check
git status --short
~~~

Expected: all locally available tests and checks pass and the worktree is clean. Docker is not installed on this workstation, so Compose validation and both platform smoke builds must be observed in GitHub Actions rather than claimed locally.

- [ ] **Step 2: Push the reviewed branch, merge through main, and apply repository policy**

Push the feature branch and open a pull request:

~~~powershell
git push origin feat/placegame-automation
gh pr create --base main --head feat/placegame-automation --title "Build PlaceGame MCP automation service" --body-file docs/superpowers/specs/2026-08-17-placegame-github-onessh-deployment-design.md
gh pr checks --watch
~~~

After the required CI check succeeds, merge without rewriting history:

~~~powershell
gh pr merge --merge --delete-branch=false
gh api --method PUT repos/fengzaixing401/placegame-mcp/branches/main/protection --input .github/branch-protection.json
~~~

If GitHub returns an account-tier error for private branch protection, report that exact external limitation and leave the payload committed; do not claim protection is active.

Verify:

~~~powershell
gh repo view fengzaixing401/placegame-mcp --json visibility,defaultBranchRef
gh api repos/fengzaixing401/placegame-mcp/branches/main/protection
~~~

Expected: repository visibility is PRIVATE, main is default, and the required context is required when the account tier supports protection.

- [ ] **Step 3: Verify release publication and immutable manifest**

Wait for Release on main, download its digest artifact, and inspect the manifest:

~~~powershell
$run = gh run list --repo fengzaixing401/placegame-mcp --workflow Release --branch main --limit 1 --json databaseId,status,conclusion | ConvertFrom-Json
if ($run.status -ne 'completed' -or $run.conclusion -ne 'success') { throw 'release workflow did not succeed' }
$sha = (git rev-parse origin/main).Trim()
gh run download $run.databaseId --repo fengzaixing401/placegame-mcp --name "release-digest-$sha" --dir .superpowers/release
$digest = (Get-Content .superpowers/release/release-digest.txt | Select-Object -First 1).Trim()
if ($digest -notmatch '^sha256:[0-9a-f]{64}$') { throw 'invalid release digest' }
~~~

Expected: Release succeeds, the artifact digest is valid, and the workflow's manifest-verification and BuildKit attestation checks succeeded. Private-registry inspection is repeated on the server after its read-only GHCR credential is installed.

- [ ] **Step 4: Bootstrap and deploy through OneSSH**

Before this step, stop and request the operator to install the root GHCR read credential through a secure prompt. Never request the token in chat.

Use OneSSH hosts_list to select the previously inventoried Singapore host. Use OneSSH exec for these bounded actions:

1. read-only recheck uname -m, docker version, docker compose version, and current listeners on 80/443;
2. use the privileged OneSSH entry to run the reviewed bootstrap.sh with the release digest, creating the fixed placegame-deploy identity when absent;
3. install the operator-controlled OneSSH public key for placegame-deploy and create a separate routine OneSSH host entry using that username;
4. through the routine entry, prove unrestricted sudo and direct Docker access fail while `sudo /opt/placegame-mcp/bin/deploy sha256:DIGEST` is allowed;
5. inspect `ghcr.io/fengzaixing401/placegame-mcp@DIGEST` and require linux/amd64 and linux/arm64 descriptors plus provenance/SBOM attestations;
6. verify docker compose -p placegame-mcp ps;
7. verify curl --fail http://127.0.0.1:18080/health/live;
8. verify curl --fail http://127.0.0.1:18080/health/ready;
9. verify docker inspect reports the requested RepoDigest;
10. verify docker port reports only 127.0.0.1:18080 for app and no port for postgres; and
11. verify every pre-existing non-PlaceGame container remains running with unchanged published ports.

Expected: ARM64 image is healthy, dedicated PostgreSQL is healthy, state directories are root-owned, secrets are mode 0600, and no existing container is changed.

- [ ] **Step 5: Prove persistence and rollback, then commit only tested fixes**

Create a harmless durable test record through the application administration API, restart only the app container, and prove the record remains. Perform the production rollback drill only when two reviewed, functional releases with different source commits and manifest digests exist. Deploy the newer legitimate release, then invoke the older digest through the fixed routine command:

~~~text
sudo /opt/placegame-mcp/bin/deploy sha256:PREVIOUS_64_HEX_DIGEST
~~~

The actual command uses the verified previous digest from previous.env, not the literal example text above.

Do not manufacture a second image through build-label or metadata-only churn. If the first rollout has only one legitimate release, record the persistence and first-deployment acceptance now and leave the rollback-drill item explicitly pending until the next functional release; do not claim the full rollback acceptance criterion has passed.

Verify:

- current.env contains the previous immutable digest;
- /health/live and /health/ready return HTTP 200;
- the durable test record remains in PostgreSQL;
- the release record contains deploy and rollback outcomes without secrets;
- PostgreSQL still has no host port;
- app still binds only 127.0.0.1:18080; and
- all non-PlaceGame containers remain unchanged.

If verification exposes a defect, first add a failing focused test, implement the minimal fix, rerun the covering tests and full deployment suite, then commit only those tested files with subject fix: harden deployment acceptance. If no defect is found, do not create an empty commit.

## Deployment Self-Review Checklist

- Spec coverage: Repository Bootstrap covers private source upload; Task 1 covers the image, secret-file database URL, loopback app, dedicated PostgreSQL, networks, volumes, and no-browser runtime; Task 2 covers digest validation, backup, migration, health, atomic switch, audit, and rollback; Tasks 3-4 cover read-only CI, both architectures, Trivy, tags, SBOM, provenance, least privilege, and release digest; Task 5 covers root ownership, secret generation, restricted sudo, GHCR credential handling, and 1Panel deferral; Task 6 covers GitHub policy, GHCR publication, OneSSH deployment, persistence, isolation, and rollback acceptance.
- Placeholder scan command: rg -n -i "T[O]DO|T[B]D|F[I]XME|implement[ ]later|fill[ ]in|write[ ]tests[ ]for[ ]the[ ]above|appropriate[ ]error[ ]handling|similar[ ]to[ ]task" docs/superpowers/plans/2026-08-17-placegame-github-onessh-deployment.md; expected output is empty.
- Type and contract check: uv run pyright src tests deploy reports zero errors; Settings.read_database_url, ReleaseState, CommandRunner, Deployer.deploy, image repository, digest format, Compose project, ports, secrets, and workflow job names match the frozen interfaces above.
- Workflow check: every uses reference matches a full 40-character SHA; CI has contents:read only and no push; Release alone has packages:write and publishes both architectures only after scans.
- Fresh local verification: uv run pytest -q, uv run pyright src tests deploy, frontend type/test/build commands, git diff --check, and a clean worktree are required before GitHub or server mutation. Compose and platform checks run in GitHub Actions because the workstation has no Docker installation.
- Fresh external verification: GitHub visibility and workflow conclusions, GHCR manifest descriptors/attestations, OneSSH service state, loopback health, PostgreSQL non-exposure, state persistence, rollback, and unchanged pre-existing containers must all be observed before completion.
