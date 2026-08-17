# PlaceGame GitHub, GHCR, and OneSSH Deployment Design

## Purpose

This document defines the delivery path for the PlaceGame MCP service:

- source hosted in a private GitHub repository;
- hardened GitHub Actions validation and multi-platform image publication;
- private images stored in GitHub Container Registry (GHCR);
- explicit, digest-pinned deployment through the configured OneSSH MCP server;
- a dedicated PostgreSQL 16 service on the Singapore Docker host; and
- the existing 1Panel OpenResty installation retained as the public edge.

It is a deployment overlay for the approved core, inventory, and WebUI designs.
For the Singapore target, it supersedes the earlier requirement that the
PlaceGame Compose stack own ports 80 and 443 through Caddy. All application,
database, credential, audit, scheduling, and direct-game-API safety constraints
remain unchanged.

## Confirmed Decisions

- GitHub owner and repository: `fengzaixing401/placegame-mcp`.
- Repository visibility: private.
- Container registry: private GHCR package
  `ghcr.io/fengzaixing401/placegame-mcp`.
- Supported image platforms: `linux/amd64` and `linux/arm64`.
- Delivery model: GitHub Actions builds; an authorized agent deploys through
  OneSSH. GitHub Actions never connects to the server.
- Release policy: pull requests validate without publishing; `main` publishes
  continuous images; `v*` tags publish releases.
- Deployment identity: immutable OCI manifest digest, never a mutable tag.
- Server edge: reuse 1Panel OpenResty on ports 80 and 443.
- Initial application binding: `127.0.0.1:18080`; no domain is configured in
  the first deployment.
- Database: a dedicated `postgres:16-alpine` service and volume in the
  PlaceGame stack. The existing `subboost-db` container is not reused.
- Supply-chain policy: tests, type checks, vulnerability gates, SBOMs, and
  build provenance are required.

## Observed Server Baseline

A read-only OneSSH inventory identified the Singapore target as:

- Ubuntu with Linux kernel `6.8.0-1054-oracle`;
- `aarch64` CPU architecture;
- Docker Engine `27.0.3`, active;
- Docker Compose `2.28.1`;
- ports 80 and 443 already listening on IPv4 and IPv6; and
- 23 running containers, including 1Panel OpenResty and unrelated PostgreSQL,
  MariaDB, Redis, and application workloads.

The PlaceGame deployment must not stop, recreate, reconfigure, or join the
networks of those existing workloads. The server's ARM64 architecture is the
deployment platform; the AMD64 image exists for portability and independent
runtime verification.

## Delivery Architecture

```text
developer branch / pull request
             |
             v
     GitHub Actions CI
   tests, types, migrations,
  scans, amd64/arm64 builds
             |
        main or v* only
             v
       private GHCR
  manifest digest + SBOM +
       build provenance
             |
     authorized agent request
             v
        OneSSH MCP
  fixed digest-only deploy command
             |
             v
 Singapore Docker host
 app + dedicated PostgreSQL
 bound to 127.0.0.1:18080
             |
       configured later
             v
 existing 1Panel OpenResty
```

No SSH private key or server credential is stored in GitHub. No GitHub Actions
job runs a remote shell. OneSSH is the only deployment control plane in this
design.

## GitHub Repository Controls

The repository is created as private and the existing local history is pushed
without rewriting commits. The default branch is `main`.

Recommended branch controls are:

- require the CI workflow before merging to `main`;
- prevent force pushes and deletion of `main`;
- require pull requests once more than one maintainer is active; and
- enable secret scanning and Dependabot alerts when supported for the private
  repository.

Workflow permissions default to read-only. A publication job receives only:

- `contents: read`;
- `packages: write`;
- `id-token: write` for provenance; and
- `attestations: write` when GitHub artifact attestations are used.

All reusable third-party and GitHub actions are pinned to immutable full commit
SHAs. Dependency update tooling may propose SHA updates through pull requests.

## Continuous Integration Workflow

`.github/workflows/ci.yml` runs for pull requests and branch pushes that do not
publish a release. It performs:

1. install Python 3.12 and the pinned `uv` toolchain;
2. install dependencies from `uv.lock` with lock verification;
3. run unit and integration tests, including Testcontainers PostgreSQL;
4. run Alembic metadata drift checks;
5. run Pyright with zero errors;
6. validate the production Compose model;
7. scan the repository and dependency lock for secrets and vulnerabilities;
8. build separate AMD64 and ARM64 image outputs with Buildx/QEMU; and
9. start each platform image under its native runner or QEMU and verify the
   minimal process and health endpoint.

The platform jobs are independent and fail the workflow if either image cannot
build or start. Pull-request jobs do not authenticate to GHCR with write access
and do not push images.

## Release Workflow

`.github/workflows/release.yml` runs after the same mandatory checks for:

- pushes to `main`; and
- Git tags matching `vMAJOR.MINOR.PATCH`.

Each platform image is built separately, scanned, and pushed by digest. A final
job creates one OCI manifest list only after both platform jobs pass. The
workflow records the manifest digest as its deployment output and build
artifact.

Tag behavior is exact:

- `main` publishes `main` and `sha-COMMIT`, where `COMMIT` is the full Git
  commit SHA;
- `v1.2.3` publishes `1.2.3`, `1.2`, `1`, and `latest`; and
- mutable tags are informational only and are never accepted by the deployment
  script.

The release gate rejects fixed, actionable vulnerabilities at severity HIGH or
CRITICAL. Unfixed upstream findings remain visible in the report and SBOM but
do not make an otherwise unpatchable release impossible. Both platform images
receive an SPDX SBOM and provenance tied to the source commit and workflow run.

Release concurrency is serialized. A newer release never cancels one that has
started publishing a manifest, preventing partially published tag sets.

## Server Stack

The server deployment root is `/opt/placegame-mcp` with this owned layout:

```text
/opt/placegame-mcp/
  bin/deploy
  compose.yaml
  current.env
  previous.env
  releases/
  backups/
  secrets/
```

The Compose project name is fixed to `placegame-mcp`. It contains:

- `app`: the digest-pinned PlaceGame image;
- `migrate`: a one-shot service using the same image and configuration;
- `postgres`: `postgres:16-alpine`, reachable only on the private Compose
  network; and
- named volumes for PostgreSQL data and application-owned durable artifacts.

Only `app` publishes a host port, exactly `127.0.0.1:18080` to its internal
HTTP port. PostgreSQL has no `ports` entry. The stack does not include Caddy on
this target and does not modify 1Panel/OpenResty. Domain, certificate, and
OpenResty virtual-host configuration are explicitly deferred to the operator.

Containers use health checks, restart policies, resource limits, read-only
filesystems where compatible, dropped Linux capabilities, and non-root users.
The application and database receive separate service identities and networks.

## Secrets and Registry Authentication

GitHub Actions uses its ephemeral `GITHUB_TOKEN` to publish packages. It does
not hold application or server secrets.

The server uses a dedicated GHCR credential with only `read:packages`. The
credential is installed through an operator-controlled channel and stored in a
root-owned Docker credential store or configuration file with mode `0600`, for
use only by the restricted root-owned deployment script. It is never placed in
Compose environment values, repository files, deployment arguments, agent
messages, or logs.

Application secrets live under `/opt/placegame-mcp/secrets` with least-privilege
ownership and are mounted read-only as Docker secrets. They include:

- the 256-bit PlaceGame encryption master key;
- the dedicated PostgreSQL password; and
- initial WebUI administration bootstrap material when the WebUI is installed.

The application reads secret files rather than interpolating secret values into
command lines. Backups of the database and the encryption master key use
separate operator-controlled channels, as required by the core design.

## Digest-Only Deployment Transaction

OneSSH invokes the fixed script `/opt/placegame-mcp/bin/deploy`. The script
accepts a GHCR OCI manifest digest matching `sha256:` followed by exactly 64
lowercase hexadecimal characters. It does not accept a repository, tag,
command fragment, Compose override, or arbitrary environment value.

The deployment transaction is:

1. acquire an exclusive deployment lock with `flock`;
2. verify Docker, Compose, free disk space, secret files, and GHCR
   authentication;
3. inspect the manifest and prove it contains `linux/arm64`;
4. record the current digest and source revision as the rollback candidate;
5. pull the requested image by digest;
6. wait for the existing PostgreSQL service or initialize the dedicated one;
7. create a timestamped compressed `pg_dump` before schema changes;
8. run the one-shot `migrate` service and require a zero exit status;
9. atomically replace `current.env` with the new image digest;
10. recreate only PlaceGame services;
11. poll `http://127.0.0.1:18080/health/live` and `/health/ready` within a
    bounded timeout; and
12. record source SHA, image digest, migration revision, health results, and
    deployment outcome in a secret-free release record.

The script uses structured argument passing and never evaluates generated shell
text. A failed preflight, pull, backup, or migration leaves the running app
unchanged. A failed post-switch health check restores `previous.env`, recreates
the previous application image, and verifies its health.

## Migration and Rollback Policy

Database migrations are forward-only and follow expand/contract compatibility:

- a release may add nullable columns, tables, or indexes before using them;
- destructive changes are separated by at least one compatible release;
- the immediately previous application image must remain compatible with the
  upgraded schema; and
- migration downgrade is not part of automatic rollback.

This lets an unhealthy application release roll back its image without an
unsafe automatic database restore. Database restoration is a separate,
operator-confirmed recovery action using the pre-migration backup. The first
production rollout includes a successful image rollback drill.

## OneSSH Authorization Boundary

The configured OneSSH MCP server remains the remote execution boundary. A
one-time privileged bootstrap installs the root-owned Compose definition,
deployment script, restricted sudo rule, directories, and secret-file
permissions. The OneSSH deployment identity cannot modify `bin/deploy`,
`compose.yaml`, or `secrets/`; it may invoke the root-owned script through that
exact sudo rule and write only release records selected by the script.

Agent deployment instructions name the host and invoke only the fixed deploy
script with a verified digest. Routine deployment does not use raw interactive
SSH, does not edit 1Panel configuration, and does not enumerate or print secret
files.

OneSSH command output is limited to:

- release and image identifiers;
- Compose service state;
- migration revision and exit status;
- bounded health-check output; and
- rollback status.

The deployment identity does not receive unrestricted Docker-group membership,
because Docker access is equivalent to root on the host. The restricted sudo
rule permits only the root-owned digest deployment script. Broader host
administration is not part of the steady-state deployment role.

## Initial Exposure and Later Domain Setup

The initial deployment is intentionally reachable only from the server through
`127.0.0.1:18080`. Acceptance uses local HTTP health checks through OneSSH.

When the operator later chooses a domain, 1Panel OpenResty will terminate TLS
and proxy to the loopback port. That separate step must preserve streaming for
MCP and SSE, set forwarded headers, enforce request-size and timeout limits,
and expose neither PostgreSQL nor internal health details. This design does not
select a domain or mutate the current OpenResty installation.

## Verification and Acceptance

The repository delivery work is accepted only when all of the following are
proven:

- the private GitHub repository exists and `main` contains the intended local
  history;
- branch CI passes without package write permission;
- release CI publishes one manifest with both AMD64 and ARM64 descriptors;
- the manifest digest has platform SBOMs and source-linked provenance;
- the vulnerability gate has no actionable HIGH or CRITICAL finding;
- no GitHub secret contains SSH credentials or application secrets;
- OneSSH deploys an explicit digest and the server reports the same digest;
- the dedicated PostgreSQL service is healthy and publishes no host port;
- only `127.0.0.1:18080` is published by the PlaceGame project;
- live and ready health checks pass;
- state survives an application container recreation; and
- a previous-digest rollback drill succeeds without disturbing any existing
  non-PlaceGame container.

## Failure Handling

- A failed platform build prevents manifest publication.
- A failed vulnerability scan prevents that platform digest from joining the
  manifest.
- A missing or malformed digest is rejected before OneSSH performs a pull.
- A GHCR authentication failure leaves the running release untouched.
- A PostgreSQL backup or migration failure leaves the current app untouched.
- A readiness timeout restores the previous image and reports both new and
  rollback health results.
- A failed rollback stops further automated deployment and requires operator
  recovery; it never triggers an automatic database restore.
- A OneSSH or GitHub outage does not stop the currently running scheduler and
  application.

## Integration With Existing Plans

The implementation plans must be amended before application code begins:

- Core Task 8 builds a production image and an app/PostgreSQL Compose model for
  the Singapore profile instead of binding a project Caddy service to 80/443.
- WebUI Task 8 validates loopback publication and documents the later 1Panel
  streaming reverse-proxy requirements instead of editing the live 1Panel
  installation.
- A deployment implementation plan adds the GitHub workflows, server Compose
  assets, fixed deployment script, tests, GitHub repository creation, GHCR
  publication, and OneSSH acceptance sequence.
- The earlier MCP token preflight mismatch is resolved by using a 64-character
  lowercase hexadecimal `str` for `token_digest` and
  `McpTokenStore.find_by_digest` consistently.

## Non-Goals

- configuring a public domain or editing 1Panel/OpenResty;
- installing a self-hosted GitHub Actions runner;
- deploying from GitHub Actions over SSH;
- making the source repository or GHCR package public;
- reusing another application's PostgreSQL container or database;
- automatically restoring a database after an application rollback; or
- changing any existing non-PlaceGame container on the Singapore server.
