# Deployment Task 2 Report

## Files changed

- `deploy/__init__.py`
- `deploy/placegame_deploy.py`
- `deploy/bin/deploy`
- `deploy/bootstrap.sh`
- `deploy/README.md`
- `tests/deployment/test_deploy_controller.py`
- `tests/deployment/test_bootstrap_contract.py`

## Commit

`ac82cb29d691a14f47622d6cbb3bb005f3d9c239` (`feat: add digest-only deployment controller`)

## RED

Command: `uv run pytest tests/deployment/test_deploy_controller.py -q`

Output: failed before collection because `uv` is not installed (`The term 'uv' is not recognized`).

## GREEN

Command attempted: `python -m pytest tests/deployment/test_deploy_controller.py tests/deployment/test_bootstrap_contract.py -q`

Output: failed while loading the repository `tests/conftest.py` because the environment lacks the `placegame` package (`ModuleNotFoundError: No module named 'placegame'`).

## Pyright

Command: `pyright deploy/placegame_deploy.py tests/deployment`

Output: unavailable because `pyright` is not installed. Python bytecode compilation succeeded for the implementation and focused tests.

## Self-review

The controller validates exact lowercase SHA-256 digests, uses the fixed Compose project/path arguments, stages candidate state atomically, runs pull/postgres/migration/app in the required order, polls both health endpoints, promotes state only after health success, and restores only the app on failed upgrades. Bootstrap is root-only, argument-free, idempotent, restrictive with secret permissions, and generates the required URL-safe values. The runbook uses GHCR password-stdin authentication and digest-only deployment.

## Concerns

The required test and Pyright gates could not run in this checkout because `uv`, `pyright`, and the repository's `placegame` import dependencies are unavailable. No full repository suite or deployment against server state was run.

## Follow-up Fix

Verified and fixed the focused Task 2 review findings:

- Added a non-secret digest-shaped README example while retaining the variable-based secure deploy flow.
- Changed the candidate image pull to explicitly pull both `app` and `migrate`.
- Made `current.env` parsing strict: exactly one newline-terminated `PLACEGAME_IMAGE=` assignment, the fixed repository, and a lowercase 64-character SHA-256 digest; malformed or extra content is rejected uniformly.
- Reformatted `deploy/placegame_deploy.py` for readable production maintenance. Migration still runs before the app replacement try-block, so migration failure cannot trigger app replacement or rollback commands.

RED command after adding focused tests:

```powershell
& '.\\.venv\\Scripts\\python.exe' -m pytest tests/deployment/test_deploy_controller.py tests/deployment/test_bootstrap_contract.py -q
```

Output before the fix:

```text
.......FFFF.....F                                                        [100%]
5 failed, 12 passed in 3.15s
```

GREEN command:

```powershell
& '.\\.venv\\Scripts\\python.exe' -m pytest tests/deployment/test_deploy_controller.py tests/deployment/test_bootstrap_contract.py -q
```

Output:

```text
.................                                                        [100%]
17 passed in 2.51s
```

Focused static verification:

```powershell
& '.\\.venv\\Scripts\\python.exe' -m py_compile deploy/placegame_deploy.py tests/deployment/test_deploy_controller.py tests/deployment/test_bootstrap_contract.py
```

Output: succeeded. `pyright` was checked but is unavailable on this host.

## Rollback and Promotion Follow-up

The deployment controller now handles the final review findings:

- On a post-app replacement failure with a prior image, rollback `pull app` and rollback `up -d --no-deps app` are attempted independently. A failure of either rollback command is recorded through the constant `deployment_rollback_failed` message with only the rollback step; the original deployment failure is re-raised.
- On successful health checks, `state/candidate.env` is atomically promoted with `replace()` to `current.env`. No second current-state copy is written, and the candidate path is consumed.

RED command after adding focused tests:

```powershell
& '.\\.venv\\Scripts\\python.exe' -m pytest tests/deployment/test_deploy_controller.py tests/deployment/test_bootstrap_contract.py -q
```

Output before implementation:

```text
........F......F...                                                      [100%]
2 failed, 17 passed in 3.14s
```

The failures proved that candidate state was retained after a successful deploy and rollback-pull failure masked the original health error before rollback-up could run.

GREEN command:

```powershell
& '.\\.venv\\Scripts\\python.exe' -m pytest tests/deployment/test_deploy_controller.py tests/deployment/test_bootstrap_contract.py -q
```

Output:

```text
...................                                                      [100%]
19 passed in 2.43s
```

Compile command:

```powershell
& '.\\.venv\\Scripts\\python.exe' -m py_compile deploy/placegame_deploy.py tests/deployment/test_deploy_controller.py tests/deployment/test_bootstrap_contract.py
```

Output: succeeded with exit code 0.

## Sol Review Fix Report

Implemented the two requested Deployment Task 2 fixes:

- Added focused coverage that health failure attempts rollback `pull app` and `up -d --no-deps app`, and that a rollback-pull exception does not mask the original `health check failed` error.
- Promoted `state/candidate.env` with `Path.replace()` so the candidate file is consumed; the success test asserts it is absent.

Verification command:

```powershell
& '.\\.venv\\Scripts\\python.exe' -m pytest tests/deployment/test_deploy_controller.py tests/deployment/test_bootstrap_contract.py -q
```

Output:

```text
.................                                                        [100%]
17 passed in 2.45s
```

Only the covering Task 2 tests were run. No full suite, live deployment, or unrelated files were changed.

Concern: `pyright` is unavailable in this checkout; the focused Python tests provide the behavioral verification.
