# PlaceGame Core Task 4 Second Review Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining Core Task 4 identity-upgrade, plan-freshness, verifier-classification, and removal-ordering defects before Core Task 5 begins.

**Architecture:** Keep account identity and lifecycle serialization in PostgreSQL. Historical rows bind only from their stored secret, uniqueness races are contained by a savepoint, and every account-writing transaction takes a row lock after its advisory lock. Planned mutations receive a Task 5-owned authoritative fingerprint callback; Task 4 treats its result as opaque and forcibly reloads the plan on every attempt.

**Tech Stack:** Python 3.12, asyncio, Pydantic v2, SQLAlchemy 2 async ORM, Alembic, PostgreSQL 16, pytest, Pyright, Docker, OneSSH.

## Global Constraints

- Work only in `D:\Ai\placegame-mcp\.worktrees\placegame-automation` on `feat/placegame-automation`.
- Read `docs/superpowers/specs/2026-08-18-placegame-core-plan-fingerprint-contract-amendment-design.md`, `.superpowers/sdd/task-4-brief.md`, `.superpowers/sdd/task-4-review-fix.md`, and `.superpowers/sdd/task-4-report.md` before editing.
- Do not contact the real PlaceGame service, use browser automation, or build the PlaceGame application image.
- Do not run pytest or Pyright locally. All RED/GREEN and final gates run through OneSSH host `新加坡`.
- Use disposable `ghcr.io/astral-sh/uv:0.8.13-python3.12-bookworm-slim` runners and `postgres:16-alpine` on a unique internal Docker network with no published database port, PostgreSQL tmpfs storage, no named volume, and no Docker socket mount.
- Connect the runner to its default bridge for downloads before connecting it to the internal database network. Install `libatomic1` only in the disposable runner before Pyright.
- Use exact unique `placegame-core4-r2-*` resource names and an exact `/tmp/placegame-core4-r2-*` source path. Clean and prove the exact containers, network, volumes, and `/tmp` path absent after every remote run.
- Preserve stable `GameSchemaMismatch`, `AccountIdentityConflict`, `PlanPreconditionFailed`, cancellation identity, and secret redaction. Never persist credentials, tokens, raw game bodies, raw resolver state, verifier internals, or exception text.
- Never nest the external identity advisory lock with a local account advisory lock.
- Do not push. After all fresh final gates pass, create one focused code commit after documentation commit `6dcdd57`.

---

### Task 1: Repair The Remaining Core Task 4 Review Findings

**Files:**
- Modify: `src/placegame/accounts/repository.py`
- Modify: `src/placegame/accounts/service.py`
- Modify: `src/placegame/game/schemas.py`
- Modify: `tests/fakes/game_server.py`
- Modify: `tests/unit/test_accounts.py`
- Modify: `tests/unit/test_game_client.py`
- Modify: `tests/integration/test_account_isolation.py`
- Modify: `tests/integration/test_migrations.py`
- Modify: `.superpowers/sdd/task-4-report.md`

**Interfaces:**
- Consumes: `GameApi`, `ActionPlan`, `GameAccount`, `AccountRepository`, `SecretBox`, `AccountIdentityConflict`, `PlanPreconditionFailed`, and the existing transaction-scoped `account_lock`/`identity_lock` contexts.
- Produces: `StateFingerprintResolver = Callable[[GameApi], Awaitable[str]]` and the approved keyword-only `AccountService.mutate` parameter `state_fingerprint: StateFingerprintResolver | None = None` between `plan_id` and `verify`.
- Produces: `AccountRepository.get_for_update(session, account_id) -> GameAccount | None` and `AccountRepository.has_unresolved_identity(session) -> bool`.
- Preserves: `MutationOutcome[T]`, `verify(api, T | None)`, all public lifecycle signatures, and nullable `GameAccount.game_account_id` for historical migration compatibility.

- [ ] **Step 1: Add historical identity and concurrent enrollment RED tests**

Add a migration fixture that explicitly downgrades the disposable test database to base, upgrades only to `001_core`, inserts historical credential/token rows without `game_account_id`, upgrades to `head`, and leaves the database at `head` in `finally`. Seal inserted secrets with the real test `SecretBox` and the row-bound `encrypted_aad` helpers.

The fixture returns a `LegacyIdentityEnvironment` test dataclass with `service`,
`sessions`, `fake`, `first_id`, `second_id`, `first_token`, `second_token`, and
`shared_identity`. Add tests with these assertions:

```python
async def test_unresolved_historical_row_blocks_new_enrollment(
    legacy_identity_env,
):
    environment = legacy_identity_env
    unrelated_id = uuid4()
    unrelated_token = f"unrelated-{unrelated_id.hex}"
    environment.fake.register_token(unrelated_id, unrelated_token)
    with pytest.raises(AccountIdentityConflict):
        await environment.service.add_token_only(
            "new", unrelated_token, actor=ADMIN
        )
    async with environment.sessions() as session:
        rows = (await session.scalars(select(GameAccount))).all()
    assert {row.id for row in rows} == {
        environment.first_id,
        environment.second_id,
    }

async def test_historical_replacement_binds_stored_identity_before_proposal(
    legacy_identity_env,
):
    environment = legacy_identity_env
    other_id = uuid4()
    other_token = f"other-{other_id.hex}"
    environment.fake.register_token(other_id, other_token)
    async with environment.sessions() as session:
        before = await session.get(GameAccount, environment.first_id)
        assert before is not None
        before_token = before._session_token
    with pytest.raises(AccountIdentityConflict):
        await environment.service.update_token_only(
            environment.first_id, other_token, actor=ADMIN
        )
    async with environment.sessions() as session:
        row = await session.get(GameAccount, environment.first_id)
    assert row is not None
    assert row.game_account_id == str(environment.shared_identity)
    assert row._session_token == before_token

async def test_concurrent_historical_binding_returns_sanitized_conflict(
    legacy_identity_env,
):
    environment = legacy_identity_env
    results = await asyncio.gather(
        environment.service.update_token_only(
            environment.first_id, environment.first_token, actor=ADMIN
        ),
        environment.service.update_token_only(
            environment.second_id, environment.second_token, actor=ADMIN
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    assert sum(isinstance(value, AccountIdentityConflict) for value in results) == 1
    async with environment.sessions() as session:
        rows = (await session.scalars(select(GameAccount))).all()
    assert sum(
        row.game_account_id == str(environment.shared_identity) for row in rows
    ) == 1
```

Add sequentially independent concurrent duplicate enrollment tests for both modes:

```python
@pytest.mark.parametrize("mode", ["token", "credentials"])
async def test_concurrent_duplicate_enrollment_has_one_winner(account_env, mode):
    game_identity = uuid4()
    token = f"concurrent-{game_identity.hex}"
    username = f"user-{game_identity.hex}"
    password = f"password-{game_identity.hex}"
    if mode == "token":
        account_env.fake.register_token(game_identity, token)

        async def enroll(label):
            return await account_env.service.add_token_only(
                label, token, actor=ADMIN
            )
    else:
        account_env.fake.register_credentials(
            game_identity, username, password, token
        )

        async def enroll(label):
            return await account_env.service.add_credentials(
                label, username, password, actor=ADMIN
            )

    results = await asyncio.gather(
        enroll("first"), enroll("second"), return_exceptions=True
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    assert sum(isinstance(value, AccountIdentityConflict) for value in results) == 1
    async with account_env.sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(GameAccount)
            .where(GameAccount.game_account_id == str(game_identity))
        )
    assert count == 1
```

Add `BootstrapState` tests proving whitespace-only and 129-character account IDs raise `ValidationError`, while a one-character and 128-character ID pass.

- [ ] **Step 2: Run the historical identity RED gate on Singapore**

Run:

```text
uv run --frozen pytest tests/integration/test_migrations.py::test_unresolved_historical_row_blocks_new_enrollment tests/integration/test_migrations.py::test_historical_replacement_binds_stored_identity_before_proposal tests/integration/test_migrations.py::test_concurrent_historical_binding_returns_sanitized_conflict tests/unit/test_accounts.py::test_concurrent_duplicate_enrollment_has_one_winner tests/unit/test_game_client.py::test_bootstrap_account_identity_bounds -q
```

Expected: the historical and schema tests fail because enrollment ignores unresolved rows, explicit replacement defines a historical identity, concurrent lazy binding leaks a database uniqueness failure, and `BootstrapState` accepts an invalid authoritative identity. The duplicate-enrollment characterization may already pass because the identity advisory lock exists; record it separately rather than misreporting it as RED. Collection/fixture errors are not acceptable RED evidence.

- [ ] **Step 3: Implement stored-secret-first historical identity binding**

In `AccountRepository`, add:

```python
async def get_for_update(
    self, session: AsyncSession, account_id: UUID
) -> GameAccount | None:
    return await session.scalar(
        select(GameAccount)
        .where(GameAccount.id == account_id)
        .with_for_update()
    )

async def has_unresolved_identity(self, session: AsyncSession) -> bool:
    return bool(
        await session.scalar(
            select(GameAccount.id)
            .where(GameAccount.game_account_id.is_(None))
            .limit(1)
        )
    )
```

Inside each enrollment identity-lock transaction, check `has_unresolved_identity()` before `_find_identity()`. Persist a sanitized conflict audit with `account_id=None` and reason `unresolved_historical_identity`, commit it, then raise `AccountIdentityConflict` outside the transaction. Do not try to guess whether the proposed identity belongs to a null row.

Before an explicit credential/token replacement on a null historical row, resolve the old identity from only the row's existing secret:

```python
async def _resolve_stored_identity(self, record: GameAccount) -> str:
    if record.auth_mode == "credentials":
        username = record.get_game_username(self.secret_box)
        password = record.get_password(self.secret_box)
        if not username or not password:
            raise AuthenticationRequired() from None
        _token, _expiry, _api, identity = await self._login_and_bootstrap(
            username, password
        )
        return identity

    token = record.get_session_token(self.secret_box)
    if not token:
        raise AuthenticationRequired() from None
    try:
        return (await self.game_factory(token).bootstrap()).account_id
    except SessionRejected:
        raise AuthenticationRequired() from None
```

Bind that identity before validating any proposed replacement. Credential-mode legacy rows prefer their stored renewable credentials; token-only rows use their stored token. Never persist the login token produced only for identity discovery.

Replace direct null assignment in `_bind_or_match_identity` with an existing-identity check followed by a savepoint-contained flush:

```python
existing = await self._find_identity(session, bootstrap_id)
if existing is not None and existing.id != record.id:
    await self._identity_mismatch(session, record, actor)
    return False
try:
    async with session.begin_nested():
        record.game_account_id = bootstrap_id
        await session.flush([record])
except IntegrityError:
    await session.refresh(record)
    await self._identity_mismatch(session, record, actor)
    return False
return True
```

Import `IntegrityError` only from `sqlalchemy.exc`. The raw exception must never leave the service or enter an audit. Preserve the current standalone `ensure_session` behavior: identity conflict returns an unauthenticated paused state; explicit edit methods commit the pause/audit and then raise `AccountIdentityConflict`.

Constrain `BootstrapState.account_id` to `min_length=1`, `max_length=128`, and at least one non-whitespace character with a Pydantic field constraint. Do not strip or otherwise rewrite a nonblank authoritative ID.

- [ ] **Step 4: Run the historical identity GREEN gate on Singapore**

Rerun the exact Step 2 node IDs.

Expected: all pass; one concurrent legacy row binds, the other receives only `AccountIdentityConflict`; unresolved rows block both enrollment modes; no proposed or stored secret appears in output or audits.

- [ ] **Step 5: Add plan resolver, fresh-plan, and verifier RED tests**

Update the public contract assertion to require `state_fingerprint` and add:

```python
async def test_plan_requires_authoritative_fingerprint_resolver(account_env):
    account, _ = await account_env.add_token()
    snapshot = await account_env.service.snapshot(account.id, actor=ADMIN)
    plan_id = await _add_plan(
        account_env, account.id, snapshot.state_fingerprint
    )
    with pytest.raises(PlanPreconditionFailed):
        await account_env.service.mutate(
            account.id,
            lambda api: api.idle_collect(),
            actor=SCHEDULER,
            plan_id=plan_id,
        )
    assert account_env.fake.mutation_count("idle_collect", account.id) == 0

@pytest.mark.parametrize("value", ["", "x" * 129])
async def test_plan_rejects_invalid_authoritative_fingerprint(account_env, value):
    account, _ = await account_env.add_token()
    plan_id = await _add_plan(account_env, account.id, "expected")

    async def resolver(api):
        return value

    with pytest.raises(PlanPreconditionFailed):
        await account_env.service.mutate(
            account.id,
            lambda api: api.idle_collect(),
            actor=SCHEDULER,
            plan_id=plan_id,
            state_fingerprint=resolver,
        )

@pytest.mark.parametrize("domain", ["boss", "profession", "reward"])
async def test_domain_fingerprint_is_rechecked_after_conflict(account_env, domain):
    account, _ = await account_env.add_token()
    current = {"value": f"{domain}-v1"}
    plan_id = await _add_plan(account_env, account.id, current["value"])

    async def resolver(api):
        return current["value"]

    async def operation(api):
        current["value"] = f"{domain}-v2"
        raise GameConflict("changed")

    with pytest.raises(PlanPreconditionFailed):
        await account_env.service.mutate(
            account.id, operation, actor=SCHEDULER,
            plan_id=plan_id, state_fingerprint=resolver,
        )
```

Add a retry test whose first operation attempt commits an independent-session update to the same plan's `execution_state` or `expires_at`, then raises `GameConflict`. The second attempt must reject the freshly changed plan before calling the operation again.

Add a normal-response verifier test that performs a typed fake read configured to raise `GameSchemaMismatch`. Assert the exact schema error type propagates and the mutation count remains one. Extend the fake with a focused idle-summary failure control rather than making the verifier directly raise.

Update every existing test that supplies `plan_id` and expects to reach the operation so it supplies a matching internal resolver. Tests intentionally proving a missing resolver must not use a fixture default that hides the requirement.

- [ ] **Step 6: Run the plan/verifier RED gate on Singapore**

Run only the new/changed plan and verifier node IDs.

Expected: failures show that `mutate` lacks the callback, the plan reload comes from the identity map, non-idle opaque state is not checked, and normal-response verifier schema mismatch becomes `ReconciliationRequired`. A signature mismatch is acceptable only for the first callback RED; after test collection is corrected, each behavioral RED must fail at its intended assertion.

- [ ] **Step 7: Implement the authoritative fingerprint callback and fresh plan load**

Add the approved alias and parameter:

```python
StateFingerprintResolver = Callable[[GameApi], Awaitable[str]]

async def mutate(
    self,
    account_id: UUID,
    operation: Callable[[GameApi], Awaitable[T]],
    *,
    actor: Actor,
    plan_id: UUID | None = None,
    state_fingerprint: StateFingerprintResolver | None = None,
    verify: Callable[[GameApi, T | None], Awaitable[bool]] | None = None,
) -> MutationOutcome[T]:
```

On every attempt, load `ActionPlan` once with `session.get(ActionPlan, plan_id, populate_existing=True)`. Validate existence, ownership, expiry, execution state, confirmation, and policy version before invoking the resolver. Only after ownership succeeds set `validated_plan_id`. For a plan, require the resolver, await it with the account-bound API, require `isinstance(value, str)` and `1 <= len(value) <= 128`, and compare it directly with `plan.state_fingerprint`. Do not compare the diagnostic `AccountSnapshot.state_fingerprint` to the plan.

Preserve typed `GameError` from resolver reads, including `GameSchemaMismatch`; convert invalid resolver output or a mismatch to `PlanPreconditionFailed`. A mutation without `plan_id` must neither require nor call the resolver.

In the normal-response verifier branch, add this ordering before the generic handler:

```python
except GameSchemaMismatch:
    raise
except Exception:
    raise ReconciliationRequired() from None
```

Export `StateFingerprintResolver` in `__all__`. Do not add a fixed view-section list or interpret `ActionPlan.proposed_actions` in Task 4.

- [ ] **Step 8: Run the plan/verifier GREEN gate on Singapore**

Rerun the exact Step 6 node IDs.

Expected: all pass; domain resolver and independent plan changes stop before a second mutation attempt; verifier and resolver schema mismatches remain `GameSchemaMismatch`.

- [ ] **Step 9: Add in-flight edit/removal RED tests**

Extend `FakeGameApiFactory` with an asyncio bootstrap barrier keyed by token. The control exposes a `started` event and a `release` event, and `_FakeGameApi.bootstrap()` awaits `release` only for the configured token. Add a token alias helper so two tokens can resolve to one permanent game identity without replacing the account state.

For the credential edit, test this exact sequence; the fake barrier is installed
for the credential account's current login token before starting the update:

```python
edit = asyncio.create_task(
    isolation_env.service.update_credentials(
        account.id, None, password, actor=ADMIN
    )
)
await asyncio.wait_for(bootstrap_started.wait(), timeout=2)

holder = asyncio.create_task(hold_account_lock_until_released())
removal = asyncio.create_task(
    isolation_env.service.disable_drain_remove(account.id, actor=ADMIN)
)
await asyncio.sleep(0.05)

observed = await isolation_env.service.get(account.id)
assert observed.paused_reason != "removing"  # marker waits for the edit row lock

bootstrap_release.set()
await asyncio.wait_for(edit, timeout=2)
await asyncio.wait_for(holder_acquired.wait(), timeout=2)
await wait_until_paused_reason(account.id, "removing")
removal.cancel()
with pytest.raises(asyncio.CancelledError):
    await removal
assert (await isolation_env.service.get(account.id)).paused_reason == "removing"
holder_release.set()
```

Define `hold_account_lock_until_released()` in the test with a separate session,
`account_lock`, `holder_acquired.set()`, and `await holder_release.wait()`.
Define `wait_until_paused_reason()` as a bounded 50-iteration observer loop with
`await asyncio.sleep(0.01)` and fail if the target state is not observed. The
token test repeats the explicit sequence with a fake token alias for the same
game identity and starts `update_token_only(account.id, alias_token,
actor=ADMIN)`. Always release events and gather tasks in `finally`. The
assertion proves removal intent is ordered after an already-entered edit and
remains durable even when the remover is cancelled before tombstoning.

- [ ] **Step 10: Run the removal RED gate on Singapore**

Run the two new integration node IDs.

Expected: both fail because the removal marker commits while the blocked edit has no row lock, and the edit can later clear `removing`. Timeouts, leaked tasks, or fixture hangs are not acceptable RED evidence.

- [ ] **Step 11: Add row ownership to every account-writing transaction**

Add a service helper backed by `AccountRepository.get_for_update`:

```python
async def _require_for_update(
    self, session: AsyncSession, account_id: UUID
) -> GameAccount:
    record = await self.repository.get_for_update(session, account_id)
    if record is None:
        raise AccountNotFound() from None
    return record
```

After acquiring `account_lock`, use `_require_for_update` in `update_label`, `update_credentials`, `update_token_only`, `pause`, `resume`, `ensure_session`, `_locked`, `snapshot`, `mutate`, and `_set_lifecycle`. Use it in both `disable_drain_remove` transactions; the first marker transaction intentionally takes only the row lock, while the final transaction takes advisory lock then row lock. Keep `get()` read-only with `_require()`.

This ordering must hold:

```text
ordinary writer: account advisory lock -> account row lock -> write -> commit
removal marker: account row lock -> set removing -> commit
removal final:  account advisory lock -> account row lock -> tombstone -> commit
```

The marker transaction never requests the advisory lock, so this order cannot form a lock cycle. Do not add another lock namespace.

- [ ] **Step 12: Run the removal GREEN and focused regression gates on Singapore**

First rerun the exact Step 10 node IDs. Then run:

```text
uv run --frozen pytest tests/unit/test_accounts.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py -q
```

Expected: all focused tests pass, no task remains pending, and cleanup inspection reports no exact `placegame-core4-r2-*` container/network/volume/path.

- [ ] **Step 13: Run static analysis and inspect the complete repair diff**

Run on Singapore after installing `libatomic1` in the disposable runner:

```text
uv run --frozen pyright src/placegame/accounts src/placegame/contracts.py src/placegame/errors.py src/placegame/models.py src/placegame/game/schemas.py tests/fakes/game_server.py tests/unit/test_accounts.py tests/unit/test_game_client.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py
```

Expected: `0 errors, 0 warnings, 0 informations`.

Locally run only read-only checks:

```text
git diff --check
git diff --stat 5bcb83f..HEAD
git diff 5bcb83f..HEAD -- src/placegame tests migrations
```

Review for account isolation, savepoint recovery, lock ordering, resolver freshness, cancellation, non-repetition, schema mismatch, migration upgrade behavior, foreign keys, and secret redaction.

- [ ] **Step 14: Append evidence and create the focused code commit**

Append every exact RED/GREEN command, summarized output, resource names, cleanup evidence, changed files, and self-review result to `.superpowers/sdd/task-4-report.md`.

Then commit only the implementation/test files; `.superpowers` remains ignored:

```text
git add src/placegame/accounts/repository.py src/placegame/accounts/service.py src/placegame/game/schemas.py tests/fakes/game_server.py tests/unit/test_accounts.py tests/unit/test_game_client.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py
git commit -m "fix: close upgraded account and plan races"
```

- [ ] **Step 15: Verify the exact committed candidate on Singapore**

Export the exact commit with `git archive` and run all gates in one fresh remote job:

```text
uv run --frozen pytest tests/unit/test_accounts.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py -q
uv run --frozen pytest -q
uv run --frozen pyright src/placegame/accounts src/placegame/contracts.py src/placegame/errors.py src/placegame/models.py src/placegame/game/schemas.py tests/fakes/game_server.py tests/unit/test_accounts.py tests/unit/test_game_client.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py
```

Expected: focused, full, and Pyright all exit zero. Independently inspect after job completion and report exact containers, network, volumes, and `/tmp` path absent. Do not amend the commit after this gate; any change requires a new commit and a fresh exact-commit gate.

- [ ] **Step 16: Prepare the re-review package**

Generate `.superpowers/sdd/review-5bcb83f..HEAD.diff` containing commit summaries, `git diff --check`, diffstat, and the complete `5bcb83f..HEAD` patch. Update `.superpowers/sdd/task-4-report.md` with the exact final commit and gate results. Return `DONE`, the commit hash, one-line focused/full/static results, cleanup result, and concerns. Do not push and do not mark Core Task 4 complete before the same reviewer clears every Critical and Important finding.
