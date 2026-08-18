# PlaceGame Core Task 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement strict account policies, typed durable action plans, and deterministic idle, boss, profession, and safe-reward decisions without browser automation or arbitrary game requests.

**Architecture:** Core Task 5 is split into three independently testable checkpoints. 5A owns immutable policy models and PostgreSQL version-CAS persistence. 5B owns the typed action codec, canonical fingerprints, and generic plan lifecycle transitions. 5C owns typed game-state adapters, bounded boss optimization, and the six domain planners. AccountService remains the only account-scoped mutation boundary; it performs generic plan checks and terminal transitions but never interprets action payloads.

**Tech Stack:** Python 3.12, Pydantic 2, SQLAlchemy async ORM, PostgreSQL 16, pytest, Pyright, Alembic, `uv run --frozen`, and the existing OneSSH disposable Docker test runner.

## Global Constraints

- Work only in `D:\Ai\placegame-mcp\.worktrees\placegame-automation` on branch `feat/placegame-automation`.
- Do not run pytest or Pyright locally; every test and static check runs on OneSSH host `新加坡`.
- Use `ghcr.io/astral-sh/uv:0.8.13-python3.12-bookworm-slim` for the runner and `postgres:16-alpine` for PostgreSQL.
- PostgreSQL is on a unique internal Docker network, has no published port, uses tmpfs at `/var/lib/postgresql/data`, and has no named volume.
- The runner may use the default bridge only for dependency downloads, then joins the internal database network; never mount the Docker socket.
- Install `libatomic1` only immediately before Pyright.
- Use exact resource prefixes `placegame-core5a-*`, `placegame-core5b-*`, and `placegame-core5c-*`, with matching `/tmp/placegame-core5*-*` source/log paths; inspect and prove exact containers, networks, volumes, and paths are absent after each run.
- Never contact the real PlaceGame service, use browser automation, build the PlaceGame application image, or push an image/repository from these checkpoints.
- Persist only typed, sanitized JSON. Credentials, session tokens, HTTP bodies, raw resolver state, exception text, and verifier internals never enter JSONB, audits, or public DTOs.
- Unknown fields, unknown actions, missing decision fields, mixed action families, malformed game responses, stale policy versions, and fingerprint mismatches fail closed.
- Combat potions follow the approved rule: only an already active potion may be used; a required potion switch produces a `blocked` decision. Do not guess a combat-potion endpoint and never use `/api/professions/supply/equip` for combat supplies.
- Existing migrations are sufficient for 5A and 5B. Do not add a migration unless a test proves an existing column cannot represent the frozen contract and the design is amended first.

---

### Task 5A: Policy Models and PostgreSQL Version CAS

**Files:**
- Create: `src/placegame/policy/__init__.py`
- Create: `src/placegame/policy/models.py`
- Create: `src/placegame/policy/ports.py`
- Create: `src/placegame/policy/store.py`
- Modify: `src/placegame/accounts/service.py` at the `TYPE_CHECKING` policy declaration only
- Test: `tests/unit/test_policy.py`
- Extend: `tests/unit/test_accounts.py`
- Regression: `tests/integration/test_migrations.py`

**Interfaces:**

`policy.models` must export these concrete types:

```python
class AccountPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idle_threshold_minutes: int = Field(690, ge=60)
    boss_min_chance: int = Field(80, ge=50, le=98)
    personal_paid_attempts: bool = False
    world_collaboration_enabled: bool = True
    world_attempts: Literal[3] = 3
    material_reserve: int = Field(64, ge=0)
    profession_food_target: int = Field(6, ge=0)
    profession_potion_target: int = Field(12, ge=0)
    profession_horizon_hours: int = Field(12, ge=1)
    inventory_warning_percent: int = Field(85, ge=1, le=99)
    inventory_critical_percent: int = Field(95, ge=1, le=100)
    inventory_auto_quality_ceiling: Literal["white", "green", "blue"] = "blue"
    inventory_keep_item_ids: frozenset[str] = frozenset()
    inventory_protected_affixes: frozenset[str] = frozenset()
    warehouse_auto_deposit_types: frozenset[str] = frozenset({"boss_material", "profession_material"})
    safe_reward_claims: bool = True

class VersionedPolicy(AccountPolicy):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(ge=1)

class PolicyConflict(AccountError):
    pass
```

`AccountPolicy` has an after-validator requiring `inventory_critical_percent >= inventory_warning_percent`. `PolicyConflict` has a fixed, payload-free message. Reuse `PolicyUnavailable` from `src/placegame/errors.py` for missing, corrupt, or divergent storage.

`policy.ports` must export:

```python
class ServerIdleCapacityReader(Protocol):
    async def __call__(self, account_id: UUID) -> int: ...

class PolicyService(Protocol):
    async def get(self, account_id: UUID) -> VersionedPolicy: ...
    async def save(
        self, account_id: UUID, policy: AccountPolicy, expected_version: int, *, actor: Actor
    ) -> VersionedPolicy: ...
    async def server_idle_capacity(self, account_id: UUID) -> int: ...
```

`PostgresPolicyService` accepts an `async_sessionmaker[AsyncSession]`, a `ServerIdleCapacityReader`, and an optional existing `AccountRepository`. It must not import `AccountService`; composition supplies a callback that acquires the existing authenticated account lock once, reads `locked.api.idle_summary().capacity_seconds`, and returns it.

- [ ] **Step 1: Write the failing policy and persistence tests.**

Add these tests to `tests/unit/test_policy.py` with real SQLAlchemy sessions from the existing `account_env` fixture:

```python
def test_account_policy_defaults_and_rejects_unknown_or_invalid_values():
    assert AccountPolicy().idle_threshold_minutes == 690
    with pytest.raises(ValidationError):
        AccountPolicy.model_validate({"unexpected": True})
    with pytest.raises(ValidationError):
        AccountPolicy.model_validate({"inventory_warning_percent": 96, "inventory_critical_percent": 95})

async def test_get_returns_virtual_version_one_default_without_row(account_env):
    account, _ = await account_env.add_token()
    policy = await account_env.policy.get(account.id)
    assert policy.version == 1
    assert policy.model_dump(mode="json")["safe_reward_claims"] is True
    async with account_env.sessions() as session:
        assert await session.get(AccountPolicyRow, account.id) is None

async def test_get_fails_closed_for_malformed_or_divergent_persisted_row(account_env):
    account, _ = await account_env.add_token()
    async with account_env.sessions.begin() as session:
        session.add(AccountPolicyRow(account_id=account.id, policy={"idle_threshold_minutes": "bad"}, policy_version=1))
    with pytest.raises(PolicyUnavailable):
        await account_env.policy.get(account.id)

async def test_save_is_exact_cas_updates_both_versions_and_audits_safely(account_env):
    account, _ = await account_env.add_token()
    saved = await account_env.policy.save(account.id, AccountPolicy(material_reserve=80), 1, actor=ADMIN)
    assert saved.version == 2 and saved.material_reserve == 80
    async with account_env.sessions() as session:
        row = await session.get(GameAccount, account.id)
        policy_row = await session.get(AccountPolicyRow, account.id)
        assert row is not None and policy_row is not None
        assert row.policy_version == policy_row.policy_version == 2
        event = (await session.scalars(select(AuditEvent).where(AuditEvent.action == "policy.save"))).one()
        assert event.result == {"status": "saved", "version": 2}

async def test_stale_save_preserves_document_and_both_versions(account_env):
    account, _ = await account_env.add_token()
    await account_env.policy.save(account.id, AccountPolicy(material_reserve=80), 1, actor=ADMIN)
    with pytest.raises(PolicyConflict):
        await account_env.policy.save(account.id, AccountPolicy(material_reserve=99), 1, actor=ADMIN)
    current = await account_env.policy.get(account.id)
    assert current.version == 2 and current.material_reserve == 80

async def test_concurrent_policy_saves_have_exactly_one_winner(account_env):
    account, _ = await account_env.add_token()
    results = await asyncio.gather(
        account_env.policy.save(account.id, AccountPolicy(material_reserve=70), 1, actor=ADMIN),
        account_env.policy.save(account.id, AccountPolicy(material_reserve=71), 1, actor=ADMIN),
        return_exceptions=True,
    )
    assert sum(isinstance(value, VersionedPolicy) for value in results) == 1
    assert sum(isinstance(value, PolicyConflict) for value in results) == 1

async def test_server_idle_capacity_delegates_once_without_nested_policy_lock(account_env):
    account, _ = await account_env.add_token()
    calls: list[UUID] = []
    account_env.policy.capacity_reader = lambda account_id: calls.append(account_id) or 43200
    assert await account_env.policy.server_idle_capacity(account.id) == 43200
    assert calls == [account.id]
```

Use the existing `AccountPolicy` ORM row under an unambiguous test alias such as `AccountPolicyRow` so it is not confused with the Pydantic model. The test fixture must expose the service only after the RED test is written.

- [ ] **Step 2: Run the 5A RED gate remotely.**

Copy the worktree to `/tmp/placegame-core5a-red-20260818a`, start a uniquely named PostgreSQL container `placegame-core5a-red-20260818a-db` on network `placegame-core5a-red-20260818a-net`, and run this exact command in `placegame-core5a-red-20260818a-runner`:

```text
uv run --frozen pytest tests/unit/test_policy.py tests/unit/test_accounts.py tests/integration/test_migrations.py -q
```

Expected result is a collection/import failure for missing `placegame.policy.models` or `PostgresPolicyService`, while the pre-existing account and migration tests still collect. Capture the exit code and clean all exact resources; the final inspection must print `containers_absent network_absent volumes_absent paths_absent`.

- [ ] **Step 3: Implement strict models and policy ports.**

Create `models.py` with the exact fields above and the validator below:

```python
@model_validator(mode="after")
def ordered_inventory_thresholds(self) -> Self:
    if self.inventory_critical_percent < self.inventory_warning_percent:
        raise ValueError("critical inventory threshold must be >= warning threshold")
    return self
```

Create `ports.py` with the exact protocols above. Replace the `TYPE_CHECKING` forward declaration in `accounts/service.py` with `from placegame.policy.models import VersionedPolicy`, retaining the consumer-owned `PolicyProvider` protocol and `FailClosedPolicyProvider` behavior.

- [ ] **Step 4: Implement transactional `PostgresPolicyService`.**

`get` must execute one outer join over `GameAccount` and ORM `AccountPolicy`, and apply these rules in order: missing account, account version below 1, missing row with account version 1 (return `VersionedPolicy(version=1, **AccountPolicy().model_dump())` without insert), missing row with any other version, non-dict JSON, malformed policy, or row/account version disagreement. All failure cases raise `PolicyUnavailable` without including database content in the message.

`save` must use one `sessions.begin()` transaction, `account_lock(session, account_id)`, `GameAccount` `SELECT ... FOR UPDATE`, and policy-row `SELECT ... FOR UPDATE`. Require `expected_version >= 1` and exact equality with both stored versions. Insert a missing policy row only for expected version 1; otherwise update the existing row. Write sanitized `policy.model_dump(mode="json")` and `expected_version + 1` to both version columns, flush, add a same-transaction audit with `{\"status\": \"saved\", \"version\": next_version}` or `{\"status\": \"conflict\", \"error\": \"PolicyConflict\"}`, then return immutable `VersionedPolicy`.

The conflict control flow must not raise from inside `sessions.begin()`, because that would roll back the conflict audit. Store a local conflict marker, add the sanitized audit without changing either policy row, leave the transaction context normally so the audit commits, then raise `PolicyConflict` outside the context. Success still commits the document, both versions, and its audit atomically.

The composition callback for `ServerIdleCapacityReader` is the only game dependency. `server_idle_capacity` awaits it once, accepts only an `int >= 0`, and raises `PolicyUnavailable` for bool, float, negative, or callback errors. It acquires no policy or account lock itself.

- [ ] **Step 5: Add policy-update plan invalidation coverage and run the 5A GREEN gate.**

Extend `tests/unit/test_accounts.py` with a real version-1 plan, save policy to version 2, call `AccountService.mutate` using that plan, assert `PlanPreconditionFailed`, zero fake mutation calls, and a same-account audit reference. Keep the existing cross-account null-plan audit assertion. Preserve the JSON-object migration rejection test.

Run remotely with exact command:

```text
uv run --frozen pytest tests/unit/test_policy.py tests/unit/test_accounts.py tests/integration/test_migrations.py -q
```

Expected result: all focused tests pass. Before the reviewer gate, run:

```text
uv run --frozen pyright src/placegame/policy src/placegame/accounts/service.py src/placegame/accounts/repository.py src/placegame/models.py src/placegame/contracts.py src/placegame/errors.py tests/unit/test_policy.py tests/unit/test_accounts.py tests/integration/test_migrations.py
```

Install `libatomic1` in the runner immediately before this command. Commit only after both commands are green:

```text
git add src/placegame/policy src/placegame/accounts/service.py tests/unit/test_policy.py tests/unit/test_accounts.py tests/integration/test_migrations.py
git commit -m "feat: add versioned account policy service"
```

Reviewer gate: reject the checkpoint if a policy row can be read with divergent versions, if concurrent saves can both succeed, if the callback nests an account lock, or if audit/error data contains policy payloads or exception text.

---

### Task 5B: Typed Plans, Canonical Fingerprints, and Lifecycle Safety

**Files:**
- Create: `src/placegame/policy/plans.py`
- Modify: `src/placegame/accounts/service.py` only for generic plan terminalization and send-state tracking
- Create: `tests/unit/test_plans.py`
- Extend: `tests/unit/test_accounts.py`
- Regression: `tests/integration/test_account_isolation.py` and `tests/integration/test_migrations.py`

**Interfaces:**

`policy.plans` exports strict frozen Pydantic models and these protocols:

```python
ActionFamily = Literal["idle", "personal_boss", "ordinary_boss", "world_boss", "profession", "safe_reward"]
DecisionState = Literal["selected", "skipped", "blocked"]
PlanState = Literal["pending", "confirmed", "executed", "failed", "reconciliation_required"]
TerminalPlanState = Literal["executed", "failed", "reconciliation_required"]
RiskClass = Literal["low", "medium", "high"]

class EstimatedCosts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    material: int = Field(0, ge=0)
    attempts: int = Field(0, ge=0)
    currency: int = Field(0, ge=0)

class PlanStore(Protocol):
    async def create(self, draft: ActionPlanDraft) -> TypedActionPlan: ...
    async def get_for_update(self, plan_id: UUID, account_id: UUID) -> TypedActionPlan: ...
    async def confirm(self, plan_id: UUID, account_id: UUID, *, actor: Actor) -> TypedActionPlan: ...
    async def mark_executing(self, plan_id: UUID, expected_state: Literal["pending", "confirmed"]) -> None: ...
    async def finish(self, plan_id: UUID, status: TerminalPlanState, result: Mapping[str, object]) -> None: ...
```

The existing `action_plans` table has no independent row-version column and 5B adds no migration. Therefore `mark_executing` uses a row lock plus the exact `expected_state` (`pending` or `confirmed`) as its compare-and-set value; it must never claim that `policy_version` is a plan version.

The registered action union contains only `IdleCollectAction`, `BossChallengeAction`, `BossAssistAction`, `ProfessionSettleAction`, `ProfessionEnqueueAction`, `ProfessionSupplyEquipAction`, `DailyClaimAction`, `QuestClaimAction`, `AchievementClaimAction`, `CodexClaimAction`, and `MailClaimAction`. Each has literal `family` and `kind`; `BossChallengeAction` uses the exact aliases and fields of `BossChallengeRequest`. No URL, endpoint, free-form body, `claim_all`, login, read operation, or specialization-select action is representable.

`SelectedDecision`, `SkippedDecision`, and `BlockedDecision` are a discriminated union on `state`; each has a stable ASCII `reason`, and `selected` requires an action. `TypedActionPlan` requires a nonempty decision list, exactly one family for all non-null actions, a valid `pgfp:v1:` fingerprint, policy version, expiry, costs, risk, and confirmation metadata. JSON persistence is `list[dict[str, object]]` with explicit aliases.

- [ ] **Step 1: Write failing codec and lifecycle tests.**

Create `tests/unit/test_plans.py` with these executable cases:

```python
def test_canonical_fingerprint_sorts_object_keys_and_declared_keyed_arrays():
    left = canonical_fingerprint("idle", {"items": [{"key": "b"}, {"key": "a"}], "seconds": 1}, keyed_arrays={"items": "key"})
    right = canonical_fingerprint("idle", {"seconds": 1, "items": [{"key": "a"}, {"key": "b"}]}, keyed_arrays={"items": "key"})
    assert left == right and re.fullmatch(r"pgfp:v1:[0-9a-f]{64}", left)

def test_canonical_fingerprint_preserves_semantic_sequence_order():
    assert canonical_fingerprint("idle", {"steps": ["a", "b"]}) != canonical_fingerprint("idle", {"steps": ["b", "a"]})

@pytest.mark.parametrize("value", [1.5, b"bytes", datetime.now(timezone.utc), float("nan"), object()])
def test_canonical_json_rejects_non_json_values(value):
    with pytest.raises(TypeError):
        canonical_json(value)

def test_typed_plan_json_round_trip_uses_aliases():
    plan = make_idle_plan()
    restored = TypedActionPlan.from_json(plan.to_json())
    assert restored == plan
    assert "idleCollect" not in json.dumps(plan.to_json())

def test_typed_plan_rejects_unknown_action_url_body_and_claim_all():
    with pytest.raises(ValidationError):
        RegisteredAction.model_validate({"family": "idle", "kind": "claim_all", "url": "/x", "body": {}})

def test_typed_plan_rejects_mixed_action_families():
    with pytest.raises(ValidationError):
        make_plan([IdleCollectAction(), BossAssistAction(boss_key="b")])
```

Extend `tests/unit/test_accounts.py` with success, deterministic failure, ambiguous outcome, post-send cancellation, terminal replay, and cross-account ownership cases. Assert terminal states and safe audit fields, and assert that replay performs zero second fake mutations. Preserve the existing resolver-after-conflict and schema-mismatch identity tests.

- [ ] **Step 2: Run the 5B RED gate remotely.**

Use source path `/tmp/placegame-core5b-red-20260818a`, network `placegame-core5b-red-20260818a-net`, database `placegame-core5b-red-20260818a-db`, and runner `placegame-core5b-red-20260818a-runner`. Run:

```text
uv run --frozen pytest tests/unit/test_plans.py tests/unit/test_accounts.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py -q
```

Expected result is failure for missing `policy.plans` types and terminal transitions, with no collection errors in existing tests. Clean and inspect exact resources before proceeding.

- [ ] **Step 3: Implement canonical JSON, typed actions, and `PlanStore`.**

Use this normalization contract; do not infer sort keys or reorder semantic lists:

```python
def canonical_json(value: object) -> bytes:
    normalized = _normalize(value)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

def canonical_fingerprint(family: ActionFamily, projection: Mapping[str, object], *, keyed_arrays: Mapping[str, str] | None = None) -> str:
    normalized = _normalize_projection(projection, keyed_arrays or {})
    envelope = {"family": family, "projection": normalized}
    return "pgfp:v1:" + hashlib.sha256(canonical_json(envelope)).hexdigest()
```

Reject `float`, `bytes`, `datetime`, NaN, unknown objects, non-string mapping keys, and unsupported containers. Sort only mappings and arrays named in `keyed_arrays`; preserve every other list order. Validate the complete prefix and lowercase 64-hex digest on plan construction and reload.

Implement `PostgresPlanStore` using the caller transaction/session. `create` validates the typed draft before writing. `get_for_update` filters by both plan ID and account ID, uses `populate_existing=True` or an explicit refresh, and validates JSONB after reload. `confirm` permits only `pending -> confirmed`, requires confirmation metadata, and stores the sanitized actor identifier. `mark_executing` locks the row and changes only the expected `pending|confirmed` state to `executing`. `finish` permits only `pending|confirmed|executing -> terminal`, writes a bounded sanitized result and `executed_at`, and never regresses a terminal row.

- [ ] **Step 4: Add generic AccountService terminal transitions.**

Keep policy resolution and ownership checks before any operation. Immediately before `await operation(api)`, set a local `send_started = True`. On successful mutation or positive verification, finish the plan as `executed`; on deterministic `PlanPreconditionFailed`, `GameConflict` exhaustion, or operation rejection before dispatch, finish as `failed`; on `AmbiguousMutation`, post-send bootstrap/verification failure, or cancellation after `send_started`, finish as `reconciliation_required`, audit the sanitized status, and re-raise the original `CancelledError` or `GameSchemaMismatch` identity. Cancellation before dispatch leaves the plan sendable and preserves the original exception.

AccountService must never deserialize or inspect `ActionPlan.proposed_actions`; it only checks account ownership, expiry, confirmation, policy version, and current execution state. The terminal write and audit share the same `session.begin()` transaction. For a missing or cross-account plan, raise `PlanPreconditionFailed`, keep `plan_id=None` in the audit, and make no plan transition.

- [ ] **Step 5: Run the 5B GREEN gate and commit.**

Run:

```text
uv run --frozen pytest tests/unit/test_plans.py tests/unit/test_accounts.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py -q
uv run --frozen pyright src/placegame/policy/plans.py src/placegame/accounts/service.py src/placegame/contracts.py src/placegame/errors.py src/placegame/models.py src/placegame/game/client.py src/placegame/game/schemas.py tests/fakes/game_server.py tests/unit/test_plans.py tests/unit/test_accounts.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py
```

The first command must pass all focused tests; the second must report `0 errors, 0 warnings, 0 informations`. Commit:

```text
git add src/placegame/policy/plans.py src/placegame/accounts/service.py tests/unit/test_plans.py tests/unit/test_accounts.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py
git commit -m "feat: add typed action plans and lifecycle safety"
```

Reviewer gate: reject any arbitrary endpoint/body escape hatch, any second mutation on replay, any terminal-state regression, or any audit containing raw exception text or resolver state.

---

### Task 5C: Typed Domain State, Planners, and Boss Optimizer

**Files:**
- Create: `src/placegame/policy/engine.py`
- Create: `src/placegame/boss_optimizer.py`
- Create: `src/placegame/rewards.py`
- Modify: `src/placegame/game/schemas.py` with typed state models for every decision field
- Modify: `src/placegame/game/client.py` only to expose typed read methods used by the adapters
- Modify: `tests/fakes/game_server.py` with deterministic state and mutation counters
- Create: `tests/unit/test_policy_engine.py`
- Create: `tests/unit/test_boss_optimizer.py`
- Create: `tests/unit/test_rewards.py`
- Extend: `tests/unit/test_accounts.py` with operation-specific resolver invalidation cases

**Interfaces:**

`policy.engine` exports these typed planner contracts:

```python
class PolicyEngine:
    async def build_idle_plan(self, locked: LockedAccount, *, now: datetime) -> TypedActionPlan: ...
    async def build_personal_boss_plan(self, locked: LockedAccount, *, now: datetime) -> TypedActionPlan: ...
    async def build_ordinary_boss_plan(self, locked: LockedAccount, *, now: datetime) -> TypedActionPlan: ...
    async def build_world_boss_plan(self, locked: LockedAccount, *, now: datetime) -> TypedActionPlan: ...
    async def build_profession_plan(self, locked: LockedAccount, *, now: datetime) -> TypedActionPlan: ...
    async def safe_reward_plan(self, locked: LockedAccount, *, now: datetime) -> TypedActionPlan: ...

class BossOptimizer:
    def __init__(self, api: GameApi) -> None: ...
    async def optimize(self, state: BossState, policy: VersionedPolicy) -> BossSelection: ...

class SafeRewardPlanner:
    def build(self, state: RewardState, policy: VersionedPolicy) -> TypedActionPlan: ...
```

All planners return one-family plans and call `canonical_fingerprint` on the exact normalized projection retained for the matching operation-specific resolver. The resolver is a `StateFingerprintResolver` passed to `AccountService.mutate`; it re-reads the same typed state and computes the same projection. A changed boss entry, world attempt counter, profession queue/recipe version, idle seconds/capacity, or reward candidate state must raise `PlanPreconditionFailed` before mutation.

Typed state adapters must require every field used for a decision and raise `GameSchemaMismatch` for missing or invalid fields. They may preserve harmless forward-compatible response fields through the existing `extra="allow"` response envelope, but planners must never read `model_extra`.

- [ ] **Step 1: Write failing 5C tests.**

Create focused tests with representative fake responses:

```python
async def test_idle_threshold_is_minimum_of_policy_and_server_capacity(engine, locked):
    locked.api.idle_summary_result = IdleSummary(accumulatedSeconds=710 * 60, capacitySeconds=720 * 60)
    plan = await engine.build_idle_plan(locked, now=SHANGHAI_NOW)
    assert plan.family == "idle" and plan.decisions[0].state == "selected"
    assert plan.decisions[0].action.kind == "idle_collect"

async def test_personal_optimizer_is_bounded_and_prefers_nightmare(engine, fake_api):
    selection = await BossOptimizer(fake_api).optimize(personal_boss_state(), policy)
    assert fake_api.preview_count <= 24
    assert selection.difficulty == "nightmare"
    assert selection.preview.predicted_win and selection.preview.chance >= policy.boss_min_chance

async def test_combat_potion_switch_is_blocked(fake_api):
    state = personal_boss_state(active_potion="haste", required_potion="guard")
    selection = await BossOptimizer(fake_api).optimize(state, policy)
    assert selection.decision.state == "blocked"
    assert fake_api.profession_supply_equip_calls == []

def test_world_window_boundaries_and_attempt_recheck(engine, world_state):
    assert engine.world_window(datetime(2026, 8, 17, 10, 0, tzinfo=SHANGHAI))
    assert not engine.world_window(datetime(2026, 8, 17, 11, 0, tzinfo=SHANGHAI))
    assert world_state.plan.decisions[0].action.kind == "boss_assist"

async def test_profession_preserves_specialization_and_five_entry_limit(engine, profession_state):
    plan = await engine.build_profession_plan(profession_state.locked, now=SHANGHAI_NOW)
    assert profession_state.selected_profession_key == "cooking"
    assert all(
        decision.action is None or decision.action.kind != "profession_select"
        for decision in plan.decisions
    )
    assert profession_state.queue_size + planned_enqueue_count(plan) <= 5

def test_safe_rewards_skip_choice_cost_overflow_and_unknown(reward_planner):
    plan = reward_planner.build(reward_state_with_choice_cost_overflow_unknown(), policy)
    assert all(decision.state != "selected" for decision in plan.decisions if decision.action is not None and decision.action.kind != "quest_claim")

async def test_typed_schema_mismatch_propagates_before_mutation(engine, locked):
    locked.api.boss_state = {"missing": "difficultyOptions"}
    with pytest.raises(GameSchemaMismatch):
        await engine.build_personal_boss_plan(locked, now=SHANGHAI_NOW)
    assert locked.api.mutation_calls == []
```

Add parameterized creation-vs-resolver equivalence tests for all six families. Each test builds a plan, changes one projection field, calls `AccountService.mutate` with the matching resolver and typed operation, and asserts zero mutation calls plus `PlanPreconditionFailed`.

- [ ] **Step 2: Run the 5C RED gate remotely.**

Use source path `/tmp/placegame-core5c-red-20260818a`, network `placegame-core5c-red-20260818a-net`, database `placegame-core5c-red-20260818a-db`, and runner `placegame-core5c-red-20260818a-runner`. Run:

```text
uv run --frozen pytest tests/unit/test_policy_engine.py tests/unit/test_boss_optimizer.py tests/unit/test_rewards.py tests/unit/test_accounts.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py -q
```

Expected result is failure for missing planners/adapters and the new typed schemas, with existing Core 1-4 tests still collecting. Clean and inspect exact resources.

- [ ] **Step 3: Add typed state models and bounded boss optimization.**

Extend `game.schemas` with explicit models for idle, boss entries/difficulty previews, attempts, equipment/material/potion state, profession specialization/queue/unlock/action recipes, inventory safety, and reward candidates. Keep response models strict for fields consumed by decisions. Add only typed read methods to `GameApi`; mutation methods remain the registered methods already present.

Implement `BossOptimizer.optimize` with these exact bounds and ordering:

```python
skill_candidates = ("output", "survival", "balanced")
buff_keys = ("none", "assault", "guard", "focus")
baseline = preview_at_most(12, skill_candidates, buff_keys, affix_key=None)
shortlist = sorted(baseline, key=lambda item: (-item.predicted_win, -item.chance, -item.player_hp_remaining_percent, item.boss_hp_remaining_percent, item.tie_key))[:3]
affix_candidates = sorted_affixes_by_multiplier(state.affixes)[:12]
```

Select the highest multiplier whose final preview predicts a win and meets `boss_min_chance`; choose material boost only after combat configuration, only on hard/nightmare, and only if at least `material_reserve` remains. Choose the lowest-score equipped eligible slot. Easy fights skip potions. A non-active required potion produces `BlockedDecision(reason="combat_potion_switch_required")`; it never calls `profession_supply_equip`.

- [ ] **Step 4: Implement idle, boss, profession, and safe-reward planners.**

`PolicyEngine` must:

- clamp idle threshold to `min(policy.idle_threshold_minutes * 60, capacity_seconds)` and select `idle_collect` only when accumulated seconds reach it;
- order personal bosses by required level descending and difficulty `nightmare`, `hard`, `normal`, consume only the free shared pool unless `personal_paid_attempts` is true, and re-read the pool after every challenge;
- select ordinary bosses only from `type == "map"` or `type == "world"` entries with explicit ordinary solo attempts, trusting server attempts, blocked reason, difficulty availability, and refresh keys;
- select world assistance only when collaboration is enabled and Beijing time is inside `[10:00,11:00)`, `[16:00,17:00)`, or `[20:00,21:00)`, using only `boss_assist` and rechecking `myAttemptCount` and `remainingAttemptCount` before each of exactly up to three attempts;
- preserve `selectedProfessionKey`, settle at five-minute maintenance boundaries, refill below two entries or six executable hours, never exceed five queue entries, plan a 12-hour horizon, and prioritize unlock milestones, configured stock (six food and twelve of each potion), then required inputs; and
- use `SafeRewardPlanner` to select only completed, individual, no-choice, no-cost, non-overflowing candidates when inventory safety is available. Choice, cost, overflow, unknown kind, and unavailable inventory safety become stable skipped/blocked reasons. Never produce a claim-all action.

Use the projections frozen in the design: idle accumulated/capacity seconds; personal boss evaluated entries, attempts, refresh, configuration, materials, equipment, potion/affix state, and preview; ordinary boss type/attempts/blocked/difficulty/refresh/preview; world instance keys/lifecycle/active/alive/attempt counters; profession specialization/queue/unlock/progress/actions/recipes/balances/recipe version; and every safe-reward candidate kind/identifier/completion/claim/choice/cost/overflow decision. Do not include policy fields or Beijing wall-clock eligibility in projections.

- [ ] **Step 5: Implement matching operation-specific resolvers and fake-server state.**

For every planner method, return or attach a resolver that fetches the same typed state, recomputes the same projection with `canonical_fingerprint`, and returns the exact `pgfp:v1:` value. Map actions only through the existing `GameApi` methods. A schema mismatch propagates unchanged; a malformed resolver result or fingerprint mismatch raises `PlanPreconditionFailed` before the operation.

Extend `tests/fakes/game_server.py` with deterministic counters for preview calls, assist calls, profession supply calls, claim calls, and every typed read. The fake must expose state mutation hooks so each equivalence test can change one projection field between plan creation and execution.

- [ ] **Step 6: Run the 5C GREEN gate and commit.**

Run:

```text
uv run --frozen pytest tests/unit/test_policy_engine.py tests/unit/test_boss_optimizer.py tests/unit/test_rewards.py tests/unit/test_accounts.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py -q
uv run --frozen pyright src/placegame/policy/engine.py src/placegame/boss_optimizer.py src/placegame/rewards.py src/placegame/policy/models.py src/placegame/policy/plans.py src/placegame/accounts/service.py src/placegame/game/client.py src/placegame/game/schemas.py tests/fakes/game_server.py tests/unit/test_policy_engine.py tests/unit/test_boss_optimizer.py tests/unit/test_rewards.py tests/unit/test_accounts.py tests/integration/test_account_isolation.py tests/integration/test_migrations.py
```

Expected: all focused tests pass and Pyright reports `0 errors, 0 warnings, 0 informations`. Commit:

```text
git add src/placegame/policy src/placegame/boss_optimizer.py src/placegame/rewards.py src/placegame/game/schemas.py src/placegame/game/client.py tests/fakes/game_server.py tests/unit/test_policy_engine.py tests/unit/test_boss_optimizer.py tests/unit/test_rewards.py tests/unit/test_accounts.py
git commit -m "feat: add deterministic core task 5 planners"
```

Reviewer gate: reject if a planner guesses an endpoint, changes a combat potion, claims a choice/cost/overflow reward, selects specialization, exceeds preview/queue/attempt bounds, bypasses AccountService, or fails to invalidate a plan when its authoritative projection changes.

---

## Core Task 5 Milestone Gate

After 5A, 5B, and 5C are independently committed and reviewed, run one final Singapore gate against a fresh disposable environment:

```text
uv run --frozen pytest tests/unit tests/integration -q
uv run --frozen pyright src tests
```

The final result must preserve the Core 1-4 baseline and report no new failures or Pyright diagnostics. Inspect `git diff --check`, `git status --short`, and the exact remote cleanup resources. Update `.superpowers/sdd/progress.md` only after the whole-branch review approves the Core Task 5 milestone:

```text
Next task: Core Task 6
```

The final Core 1-5 review must explicitly verify the deferred Task 4 cross-account null-plan audit regression and historical token-only identity regression before Task 6 begins. No scheduler, MCP, WebUI, inventory implementation, GitHub Actions, deployment, or image push is part of this plan.
