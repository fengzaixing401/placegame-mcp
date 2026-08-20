# PlaceGame GHCR and OneSSH Deployment Design

**Date:** 2026-08-19

**Status:** Approved for implementation planning after Sol review

**Goal:** Publish the server image from GitHub Actions and deploy an immutable image digest to the Singapore ARM64 Docker host through OneSSH without changing unrelated workloads.

## Decisions

- GitHub Actions publishes `ghcr.io/fengzaixing401/placegame-mcp` for `linux/arm64`. The pull-request workflow has only `contents: read`; the release workflow has `contents: read` and `packages: write` and uses Buildx with QEMU so an amd64 GitHub runner can produce an arm64 image.
- Pull requests build and smoke-test only. `main`, `v*` tags, and manual dispatch publish; deployment remains a separate OneSSH action. Release output records the immutable image digest.
- The image includes `alembic.ini` and `migrations/` so the same image can run the one-shot migration service.
- The Singapore host uses `/opt/placegame-mcp` with a dedicated Compose project: `app`, `migrate`, and `postgres`. PostgreSQL has no host port. The app publishes only `127.0.0.1:18080:8000`.
- Runtime secrets are root-owned files under `/opt/placegame-mcp/secrets`: `database_url`, `postgres_password`, `master_key`, and `mcp_token`. Compose maps them to `/run/secrets/database_url`, `/run/secrets/postgres_password`, `/run/secrets/placegame_master_key`, and `/run/secrets/placegame_mcp_token`; the app receives only the corresponding `*_FILE` paths, while PostgreSQL receives `POSTGRES_PASSWORD_FILE`. Secret values are never placed in workflow logs, image layers, Compose environment values, or OneSSH arguments.
- The server's GHCR package remains private by default. A one-time operator bootstrap installs a read-only GHCR credential in Docker's credential store; no GitHub SSH credential is used.
- OneSSH invokes only `/opt/placegame-mcp/bin/deploy` with an exact `sha256:` digest. The script accepts no repository, tag, Compose path, or shell fragment; it uses the fixed `/opt/placegame-mcp/deploy/compose.yaml`, project name `placegame-mcp`, and working directory `/opt/placegame-mcp`. It pulls that digest, records the prior app image (if any), runs migrations, starts only PlaceGame services, and checks live/ready health. A pull or migration failure leaves the running app untouched; a health failure restores only the prior app image when one exists. A first-deployment health failure leaves the new stack stopped for inspection. Database downgrade is never automatic.
- Existing containers, networks, ports, and the 1Panel/OpenResty edge are not stopped, recreated, joined, or reconfigured.

## Runtime Flow

```text
GitHub Actions -> private GHCR ARM64 image -> OneSSH digest argument
  -> /opt/placegame-mcp/bin/deploy (fixed project/workdir)
  -> docker compose pull -> migrate -> app -> /health/live,/health/ready
```

The Compose project uses a private network and a persistent PostgreSQL volume. `migrate` uses the same image and exits successfully before `app` starts. A failed pull or migration leaves the running app untouched. A failed health check restores the previous app image reference and never restores database state automatically.

## Verification

- Workflow tests validate separate PR/release permissions, triggers, QEMU/Buildx arm64 output, tags, digest publication, and that secrets are not echoed.
- Container contract tests validate the image contains migrations, the Compose model exposes only loopback port 18080, and no unrelated network is referenced.
- Deploy-script tests validate digest rejection, fixed Compose invocation, migration failure behavior, bounded health polling, and app-only rollback.
- GitHub Actions proves the published image starts and passes `/health/live` with generated CI-only secrets.
- OneSSH on `新加坡` proves the selected digest, migration completion, container health, loopback endpoint, state survival after app recreation, and unchanged unrelated-container metadata. Only resources under `/opt/placegame-mcp` are touched.

## Non-goals

- No public GHCR package assumption, GHCR deployment from GitHub Actions, SSH keys in GitHub, domain/TLS/OpenResty changes, multi-architecture release matrix, vulnerability/SBOM platform, scheduler, WebUI, RBAC, or changes to existing server workloads.
