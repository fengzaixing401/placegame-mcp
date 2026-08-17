# PlaceGame Inventory and Warehouse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hybrid inventory service that keeps every account able to collect rewards through safe sorting, policy-approved warehouse deposits, and strictly protected low-quality decomposition, while requiring an unexpired confirmed plan for valuable or irreversible actions.

**Architecture:** The inventory module is a bounded extension of the core `GameApi`, `AccountService`, `PlanStore`, audit, and MCP registries. It builds an immutable plan from a fresh authoritative snapshot, classifies every asset with explicit protection reasons, previews every destructive request, and executes under the existing per-account lock with a fingerprint/policy-version check. Inventory state is never inferred from cached UI data and no endpoint path is accepted from callers.

**Tech Stack:** Python 3.12, Pydantic 2, SQLAlchemy 2 async/PostgreSQL 16, FastAPI dependency interfaces from the core plan, httpx typed wrappers, Hypothesis property tests, pytest-asyncio, and the official MCP SDK adapter defined by the core plan.

## Global Constraints

- The operating mode is hybrid: low-risk organization, transfer, and low-quality decomposition may run automatically; high-value, irreversible, or character-build decisions require explicit confirmation.
- The module uses only these registered game operations: `POST /api/inventory/sort`, `/api/equipment/decompose-preview`, `/api/equipment/decompose`, `/api/equipment/auto-decompose-rules`, `/api/equipment/auto-decompose`, `/api/inventory/recycle-preview`, `/api/inventory/recycle`, `/api/inventory/use-item`, `/api/equipment/wear`, `/api/equipment/take-off`, `/api/equipment/toggle-lock`, `/api/warehouse/deposit`, and `/api/warehouse/withdraw`.
- Production code never exposes endpoint paths as user input; scheduled decomposition sends only equipment IDs from a successful current `decompose-preview` response.
- Automatic decomposition requires `in_bag`, not equipped or locked, white/green/blue quality, score no higher than the equipped item in the same slot, no protected affix, no missing codex entry, no unexpired plan reference, and an expected successful preview.
- If a slot has no equipped item or comparison data is incomplete, every candidate in that slot is protected with `unknown_schema`.
- Purple, orange, red, and gold decomposition; wear/take-off; withdrawal of high-value assets; consumable use; unlock; and replacement decisions require confirmation. Unbind, enhancement, reforge, quality upgrade, inheritance, and market operations are forbidden.
- Boss-difficulty materials keep at least 64 units in the bag by default; profession inputs retain queued-work plus the configured production horizon; food and boss potions remain in the bag; skill books, choice boxes, consumables, and unknown types are never automatically transferred.
- The observed warehouse request has no quantity field, so a transfer moves one complete server entry; a requested partial stack is rejected as `partial_transfer_unsupported`, and an automatic deposit is selected only when moving the complete entry preserves every bag reserve.
- At 85% occupancy the service sorts, deposits approved excess, previews safe decomposition, and targets at least 20% free capacity or 15 free slots, whichever requires more space. At 95% it marks critical pressure, blocks new boss challenges until ten slots are free, and never broadens destruction rules.
- Automatic plans expire after two minutes; manual plans expire after five minutes. Execution rechecks account lock, policy version, selected IDs, locks, qualities, scores, warehouse capacity, and preview rewards.
- A destructive plan is atomic from the service's perspective: partial mutation stops further work, refreshes state, records a high-severity audit event, and pauses automation if counts are inconsistent.
- MCP low-risk execution requires `game:operate`; manual high-value inventory execution additionally requires `inventory:confirm`; first-release batch high-value execution is not supported.
- WebUI inventory pages show protection reasons and a preview before execution, keep raw IDs out of primary labels, and never call the game directly.

---

## Execution Order

Execute this plan only after all core tasks pass. It replaces the core fail-closed `UnavailableInventorySafety` dependency with `InventoryPressureHook`, registers the reserved inventory MCP handlers, and leaves the WebUI plan until this module's acceptance suite passes. The complete order is Core → Inventory → WebUI.

## File Map

- Modify: `src/placegame/game/schemas.py` — add typed inventory, equipment, warehouse, preview, and recycle response models.
- Modify: `src/placegame/game/registry.py` and `src/placegame/game/client.py` — register the exact inventory operation/body pairs and expose `InventoryGameApi` methods.
- Modify: `src/placegame/models.py` — add durable cleanup-plan, plan-item, and inventory-alert columns while preserving core action-plan ownership.
- Create: `migrations/versions/002_inventory.py` — inventory plan and alert tables/indexes.
- Create: `src/placegame/inventory/__init__.py`, `src/placegame/inventory/types.py` — enums and immutable asset/snapshot types.
- Create: `src/placegame/inventory/classifier.py` — protection-reason classification and automatic eligibility predicate.
- Create: `src/placegame/inventory/fingerprint.py` — canonical snapshot hashing and selected-state fingerprints.
- Create: `src/placegame/inventory/warehouse.py` — allowlist, reserve, and capacity-aware transfer planning.
- Create: `src/placegame/inventory/planner.py` — pressure thresholds, cleanup plan construction, and preview selection.
- Create: `src/placegame/inventory/service.py` — plan persistence, state validation, execution, reconciliation, and audit.
- Create: `src/placegame/inventory/replacements.py` — equipment comparison/replacement suggestions with confirmation risk.
- Create: `src/placegame/inventory/mcp.py` — inventory MCP tool registrations and scope checks.
- Modify: `src/placegame/mcp/tools.py` — register the inventory tool set with the core MCP server.
- Modify: `src/placegame/app.py` — inject the inventory implementation of the core `InventorySafetyPort` and install inventory MCP handlers.
- Modify: `src/placegame/jobs/handlers.py` — call safe cleanup before idle, boss, profession, and safe-reward mutations at pressure thresholds.
- Create: `tests/inventory/conftest.py`, `tests/inventory/fixtures.py` — all seven qualities, statuses, warehouse, codex, and fake API fixtures.
- Create: `tests/inventory/test_game_api.py`, `tests/inventory/test_classifier.py`, `tests/inventory/test_planner.py`, `tests/inventory/test_execution.py`, `tests/inventory/test_replacements.py`, `tests/inventory/test_mcp.py` — unit/property/contract tests.
- Create: `tests/inventory/test_acceptance.py` — multi-account, stale-plan, capacity, and timeout acceptance tests.

## Cross-Plan Interfaces

The implementation consumes the core interfaces exactly as written below and exposes these additions to the WebUI plan:

```python
class InventoryGameApi(GameApi, Protocol):
    async def inventory_snapshot(self) -> InventorySnapshot: ...
    async def sort(self) -> SortResult: ...
    async def decompose_preview(self, equipment_ids: list[str]) -> DecomposePreview: ...
    async def decompose(self, equipment_ids: list[str]) -> DecomposeResult: ...
    async def update_auto_decompose_rules(self, patch: AutoDecomposeRulePatch) -> AutoDecomposeRules: ...
    async def auto_decompose(self) -> DecomposeResult: ...
    async def recycle_preview(self, item_id: str, amount: int) -> RecyclePreview: ...
    async def recycle(self, item_id: str, amount: int) -> RecycleResult: ...
    async def use_item(self, item_id: str) -> UseItemResult: ...
    async def wear(self, equipment_id: str) -> EquipmentMutationResult: ...
    async def take_off(self, equipment_id: str) -> EquipmentMutationResult: ...
    async def toggle_lock(self, equipment_id: str) -> EquipmentMutationResult: ...
    async def warehouse_deposit(self, entry_type: str, entry_id: str) -> WarehouseResult: ...
    async def warehouse_withdraw(self, entry_type: str, entry_id: str) -> WarehouseResult: ...
```

```python
class InventoryPlanStore(PlanStore, Protocol):
    async def create_cleanup(self, draft: CleanupDraft) -> CleanupPlan: ...
    async def get_cleanup(self, plan_id: UUID, account_id: UUID) -> CleanupPlan: ...
    async def get_cleanup_for_update(self, plan_id: UUID, account_id: UUID) -> CleanupPlan: ...
    async def create_transfer(self, snapshot: InventorySnapshot, policy: VersionedPolicy, actor: Actor, direction: Direction, asset: AssetSummary) -> TransferPlan: ...
    async def find_exact_pending_transfer(self, account_id: UUID, actor: Actor, direction: Direction, asset: AssetRef, quantity: int) -> TransferPlan | None: ...
```

```python
# src/placegame/inventory/types.py
class InventoryService(Protocol):
    async def list(self, account_id: UUID, filters: InventoryFilters, *, actor: Actor) -> InventorySnapshot: ...
    async def sort(self, account_id: UUID, *, actor: Actor) -> VerifiedSortResult: ...
    async def plan_cleanup(self, account_id: UUID, mode: CleanupMode, *, actor: Actor) -> CleanupPlan: ...
    async def get_cleanup_plan(self, account_id: UUID, plan_id: UUID, *, actor: Actor) -> CleanupPlan: ...
    async def execute_cleanup(self, account_id: UUID, plan_id: UUID, confirm: bool, *, actor: Actor) -> CleanupResult: ...
    async def plan_replacement(self, account_id: UUID, equipment_id: str, *, actor: Actor) -> ReplacementPlan: ...
    async def execute_replacement(self, account_id: UUID, plan_id: UUID, confirm: bool, *, actor: Actor) -> ReplacementResult: ...
    async def plan_item_use(self, account_id: UUID, item_id: str, *, actor: Actor) -> ItemUsePlan: ...
    async def execute_item_use(self, account_id: UUID, plan_id: UUID, confirm: bool, *, actor: Actor) -> UseItemResult: ...
    async def plan_equipment_action(self, account_id: UUID, equipment_id: str, action: Literal["take_off", "unlock"], *, actor: Actor) -> EquipmentActionPlan: ...
    async def execute_equipment_action(self, account_id: UUID, plan_id: UUID, confirm: bool, *, actor: Actor) -> EquipmentMutationResult: ...
    async def plan_recycle(self, account_id: UUID, item_id: str, amount: int, *, actor: Actor) -> RecyclePlan: ...
    async def execute_recycle(self, account_id: UUID, plan_id: UUID, confirm: bool, *, actor: Actor) -> RecycleResult: ...
    async def warehouse_transfer(self, account_id: UUID, direction: Direction, asset: AssetRef, quantity: int, confirm: bool, *, actor: Actor) -> TransferPlan | TransferResult: ...
    async def sort_locked(self, locked: LockedAccount, *, actor: Actor) -> VerifiedSortResult: ...
    async def plan_cleanup_locked(self, locked: LockedAccount, mode: CleanupMode, *, actor: Actor) -> CleanupPlan: ...
    async def execute_cleanup_locked(self, locked: LockedAccount, plan_id: UUID, confirm: bool, *, actor: Actor) -> CleanupResult: ...
```

```python
# src/placegame/inventory/types.py
CleanupMode = Literal["scheduled", "manual_safe", "manual_extended"]
Direction = Literal["deposit", "withdraw"]

class CleanupPlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    plan_id: UUID
    account_id: UUID
    expires_at: datetime
    policy_version: int
    inventory_fingerprint: str
    equipment_ids: tuple[str, ...]
    transfers: tuple[Transfer, ...]
    exclusions: tuple[Exclusion, ...]
    expected_freed_slots: int
    preview_rewards: tuple[Reward, ...]
    confirmation_required: bool
```

### Task 1: Add Typed Inventory API Contracts and Fixtures

**Files:**
- Create: `src/placegame/inventory/__init__.py`
- Modify: `src/placegame/game/schemas.py`
- Modify: `src/placegame/game/registry.py`
- Modify: `src/placegame/game/client.py`
- Create: `tests/inventory/conftest.py`
- Create: `tests/inventory/fixtures.py`
- Test: `tests/inventory/test_game_api.py`

**Interfaces:**
- Produces `InventoryGameApi.inventory_snapshot`, `sort`, `decompose_preview`, `decompose`, `update_auto_decompose_rules`, `auto_decompose`, `recycle_preview`, `recycle`, `use_item`, `wear`, `take_off`, `toggle_lock`, `warehouse_deposit`, and `warehouse_withdraw` with typed Pydantic inputs/outputs.

- [ ] **Step 1: Write failing endpoint/body contract tests**

```python
async def test_inventory_endpoints_use_fixed_paths_and_bodies(fake_game, inventory_api):
    await inventory_api.sort()
    assert fake_game.requests[-1].path == "/api/inventory/sort"
    assert fake_game.requests[-1].json_body is None
    await inventory_api.decompose_preview(["eq-1", "eq-2"])
    assert fake_game.requests[-1].path == "/api/equipment/decompose-preview"
    assert fake_game.requests[-1].json_body == {"equipmentIds": ["eq-1", "eq-2"]}
    await inventory_api.wear("eq-1")
    assert fake_game.requests[-1].json_body == {"equipmentId": "eq-1"}
    await inventory_api.use_item("item-1")
    assert fake_game.requests[-1].path == "/api/inventory/use-item"
    assert fake_game.requests[-1].json_body == {"itemId": "item-1"}
    await inventory_api.update_auto_decompose_rules(AutoDecomposeRulePatch(keepRareAffixes=True))
    assert fake_game.requests[-1].json_body == {"patch": {"keepRareAffixes": True}}

async def test_auto_decompose_is_not_used_by_scheduled_api(inventory_service, fake_game):
    await inventory_service.plan_cleanup(ACCOUNT_ID, "scheduled", actor=scheduler_actor)
    assert all(request.path != "/api/equipment/auto-decompose" for request in fake_game.requests)
```

- [ ] **Step 2: Run the focused tests and verify the expected missing-method failure**

Run: `uv run pytest tests/inventory/test_game_api.py -q`

Expected: FAIL because the inventory operation models and methods are not registered.

- [ ] **Step 3: Implement schemas and fixed registry entries**

```python
class EquipmentDecomposeRequest(BaseModel):
    equipment_ids: list[str] = Field(alias="equipmentIds", min_length=1)

class WarehouseRequest(BaseModel):
    entry_type: Literal["equipment", "item"] = Field(alias="entryType")
    entry_id: str = Field(alias="entryId", min_length=1)

class AutoDecomposeRulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    auto_recycle_qualities: list[str] | None = Field(None, alias="autoRecycleQualities")
    keep_score_above: int | None = Field(None, alias="keepScoreAbove")
    keep_rare_affixes: bool | None = Field(None, alias="keepRareAffixes")
    auto_recycle_max_level: int | None = Field(None, alias="autoRecycleMaxLevel")
    auto_recycle_protected_stats: list[str] | None = Field(None, alias="autoRecycleProtectedStats")

class AutoDecomposeRulesRequest(BaseModel):
    patch: AutoDecomposeRulePatch

class RecycleRequest(BaseModel):
    item_id: str = Field(alias="itemId", min_length=1)
    amount: int = Field(gt=0)

class UseItemRequest(BaseModel):
    item_id: str = Field(alias="itemId", min_length=1)

class HttpGameClient:  # extend the core class with these typed methods
    async def sort(self) -> SortResult:
        return await self._request("inventory_sort", SortResult)
    async def decompose_preview(self, equipment_ids: list[str]) -> DecomposePreview:
        return await self._request("decompose_preview", DecomposePreview, EquipmentDecomposeRequest(equipmentIds=equipment_ids))
    async def warehouse_deposit(self, entry_type: str, entry_id: str) -> WarehouseResult:
        return await self._request("warehouse_deposit", WarehouseResult, WarehouseRequest(entryType=entry_type, entryId=entry_id))
    async def use_item(self, item_id: str) -> UseItemResult:
        return await self._request("use_item", UseItemResult, UseItemRequest(itemId=item_id))
```

Register every allowed path with its HTTP method and response model. `inventory_snapshot` composes a fresh authoritative snapshot from the core typed bootstrap/catalog/view-section reads, requests the complete fixed inventory/equipment/warehouse/codex section set without ETags, and never adds a caller-selected section or endpoint. `auto-decompose-rules` wraps only the five patch aliases above in exactly `{patch: ...}`; recycle preview/recycle use exactly `{itemId, amount}`; use-item uses exactly `{itemId}`; wear/take-off/toggle-lock use exactly `{equipmentId}`; warehouse operations use exactly `{entryType, entryId}`. Keep `auto-decompose` and `use_item` confirmation-only; the scheduler service never calls either. Fixture responses must include capacities, entries, quality, score, slot, lock/bind state, affixes, codex flags, and item catalog metadata.

- [ ] **Step 4: Run contract tests and verify they pass**

Run: `uv run pytest tests/inventory/test_game_api.py -q`

Expected: all endpoint paths, aliases, no-body rules, and scheduled auto-decompose assertions pass.

- [ ] **Step 5: Commit the typed API checkpoint**

```bash
git add src/placegame/inventory/__init__.py src/placegame/game/schemas.py src/placegame/game/registry.py src/placegame/game/client.py tests/inventory
git commit -m "feat: add typed inventory game api"
```

### Task 2: Classify Assets and Prove Automatic Decomposition Safety

**Files:**
- Create: `src/placegame/inventory/types.py`
- Create: `src/placegame/inventory/classifier.py`
- Test: `tests/inventory/test_classifier.py`

**Interfaces:**
- Produces `ProtectionReason`, `EquipmentSummary`, `InventorySnapshot`, `classify_equipment(item, snapshot, policy, references) -> frozenset[ProtectionReason]`, and `is_auto_decomposable(item, reasons) -> bool`.

- [ ] **Step 1: Write fixture and property tests before implementation**

```python
@given(item=equipment_strategy(), reason=st.sampled_from(list(ProtectionReason)))
def test_any_protection_reason_excludes_automatic_selection(item, reason):
    assert is_auto_decomposable(item, frozenset({reason})) is False

@pytest.mark.parametrize("quality", ["white", "green", "blue", "purple", "orange", "red", "gold"])
def test_all_observed_qualities_are_classified(quality, inventory_snapshot, policy):
    item = inventory_snapshot.clean_item(quality=quality, status="in_bag", locked=False, score_below_equipped=True)
    reasons = classify_equipment(item, inventory_snapshot, policy, references=set())
    assert (quality in {"white", "green", "blue"}) == is_auto_decomposable(item, reasons)

def test_missing_equipped_slot_is_unknown_schema_protected(snapshot, policy):
    item = snapshot.item(slot="ring", status="in_bag", quality="blue")
    assert ProtectionReason.UNKNOWN_SCHEMA in classify_equipment(item, snapshot.without_equipped("ring"), policy, set())
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run pytest tests/inventory/test_classifier.py -q`

Expected: FAIL because the enums, strategy, and classifier are not defined.

- [ ] **Step 3: Implement explicit protection reasons and the all-conditions predicate**

```python
class EquipmentQuality(StrEnum):
    WHITE = "white"; GREEN = "green"; BLUE = "blue"; PURPLE = "purple"
    ORANGE = "orange"; RED = "red"; GOLD = "gold"; UNKNOWN = "unknown"

class EquipmentSummary(BaseModel):
    equipment_id: str
    name: str
    status: Literal["in_bag", "equipped", "warehouse"] | None
    slot: str | None
    quality: EquipmentQuality
    score: int | None
    locked: bool | None
    bound: bool | None
    affixes: tuple[AffixSummary, ...]
    base_attributes: dict[str, int]
    extra_attributes: dict[str, int]
    codex_missing: bool | None

class InventorySnapshot(BaseModel):
    account_id: UUID
    bag_capacity: int
    warehouse_capacity: int
    equipment: tuple[EquipmentSummary, ...]
    item_stacks: tuple[ItemStackSummary, ...]
    warehouse_entries: tuple[WarehouseEntrySummary, ...]
    equipped_by_slot: dict[str, EquipmentSummary]
    catalog_version: str
    fetched_at: datetime
    idle_reward_required_slots: int | None = None

    @property
    def free_slots(self) -> int: ...
    @property
    def can_accept_idle_rewards(self) -> bool: ...
    def filtered(self, filters: "InventoryFilters") -> "InventorySnapshot": ...
    def asset_identity_multiset(self) -> Counter[tuple[str, str, int]]: ...
    def require_asset(self, ref: "AssetRef") -> "AssetSummary": ...

class InventoryFilters(BaseModel):
    location: Literal["bag", "warehouse", "equipped", "all"] = "all"
    qualities: frozenset[EquipmentQuality] = frozenset()
    slots: frozenset[str] = frozenset()
    locked: bool | None = None
    bound: bool | None = None
    protected: bool | None = None
    text: str | None = Field(None, max_length=100)

class ProtectionReason(StrEnum):
    EQUIPPED = "equipped"
    LOCKED = "locked"
    QUALITY_ABOVE_AUTO_LIMIT = "quality_above_auto_limit"
    SCORE_UPGRADE_CANDIDATE = "score_upgrade_candidate"
    RARE_AFFIX = "rare_affix"
    CODEX_MISSING = "codex_missing"
    PROFESSION_INPUT_RESERVED = "profession_input_reserved"
    BOSS_MATERIAL_RESERVED = "boss_material_reserved"
    CONFIGURED_KEEP_ITEM = "configured_keep_item"
    PENDING_PLAN_REFERENCE = "pending_plan_reference"
    UNKNOWN_SCHEMA = "unknown_schema"

def is_auto_decomposable(item: EquipmentSummary, reasons: frozenset[ProtectionReason]) -> bool:
    return item.status == "in_bag" and not reasons
```

Use the exact core policy fields `inventory_auto_quality_ceiling`, `inventory_keep_item_ids`, `inventory_protected_affixes`, and `warehouse_auto_deposit_types`; Pydantic's `Literal["white", "green", "blue"]` rejects a higher automatic ceiling. `classify_equipment` adds `equipped`, `locked`, quality-above-ceiling, score-upgrade candidate when the item beats the equipped score, rare/configured affix, codex-missing, profession/boss reservations, pending-plan references, and `unknown_schema` whenever status, quality, slot, score, or comparison data is absent. A slot with no equipped item is always unknown-schema protected. Unknown quality and every purple-or-higher quality are excluded before any mutation is planned.

- [ ] **Step 4: Run property and fixture tests**

Run: `uv run pytest tests/inventory/test_classifier.py -q`

Expected: Hypothesis finds no counterexample and all seven quality/status cases pass.

- [ ] **Step 5: Commit the safety classifier**

```bash
git add src/placegame/inventory/types.py src/placegame/inventory/classifier.py tests/inventory/test_classifier.py
git commit -m "feat: protect inventory assets by policy reason"
```

### Task 3: Implement Capacity, Reserve, and Warehouse Planning

**Files:**
- Create: `src/placegame/inventory/fingerprint.py`
- Create: `src/placegame/inventory/warehouse.py`
- Create: `src/placegame/inventory/planner.py`
- Test: `tests/inventory/test_planner.py`

**Interfaces:**
- Produces `inventory_fingerprint(snapshot) -> str`, `plan_transfers(snapshot, policy) -> tuple[Transfer, ...]`, `capacity_pressure(snapshot, policy) -> CapacityPressure`, and `build_cleanup_draft(snapshot, policy, mode, now) -> CleanupDraft`.

- [ ] **Step 1: Write failing boundary and reserve tests**

```python
@pytest.mark.parametrize("used,capacity,pressure", [(84, 100, "normal"), (85, 100, "warning"), (95, 100, "critical")])
def test_capacity_boundaries(used, capacity, pressure, snapshot_factory, policy):
    assert capacity_pressure(snapshot_factory(used=used, capacity=capacity), policy).level == pressure

def test_target_is_twenty_percent_or_fifteen_slots_whichever_is_larger(snapshot, policy):
    draft = build_cleanup_draft(snapshot.with_capacity(100, used=90), policy, "scheduled", NOW)
    assert draft.target_free_slots == 20

def test_material_transfer_keeps_reserve_and_skips_unknown_types(snapshot, policy):
    transfers = plan_transfers(snapshot, policy)
    assert transfers[0].amount <= snapshot.material("boss-tier-1").amount - 64
    assert all(t.item_type not in {"skill_book", "choice_box", "unknown"} for t in transfers)
```

- [ ] **Step 2: Run planner tests and verify they fail**

Run: `uv run pytest tests/inventory/test_planner.py -q`

Expected: FAIL because pressure, fingerprint, transfer, and cleanup draft functions are absent.

- [ ] **Step 3: Implement canonical fingerprint and planning order**

```python
def inventory_fingerprint(snapshot: InventorySnapshot) -> str:
    canonical = snapshot.model_dump(mode="json", exclude={"fetched_at"})
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()

def target_free_slots(capacity: int) -> int:
    return max(math.ceil(capacity * 0.20), 15)
```

Count occupied equipment entries plus item-stack entries exactly as the server does. Below 85%, scheduled mode returns a no-op plan. At warning/critical pressure sort first, then transfer only allowlisted excess whole entries (consolidating an existing warehouse stack where possible), then classify and preview low-quality equipment. At critical pressure set `block_boss_challenges=True`, require ten free slots before challenge work, and preserve the non-destructive failure path. Profession horizon reserves queued inputs plus the configured horizon; food and boss-potion stacks stay in the bag. Because the endpoint cannot express quantity, a stack is selected only when its entire amount is excess; otherwise it is skipped with `partial_transfer_unsupported`. A transfer never exceeds warehouse free capacity or creates a new stack when a compatible warehouse stack can absorb it. A full warehouse converts deposits to explicit skipped actions and never selects another quality tier for decomposition.

- [ ] **Step 4: Run planner tests and verify boundary output**

Run: `uv run pytest tests/inventory/test_planner.py -q`

Expected: all three pressure levels, target-space calculation, reserve clipping, and unknown-item exclusions pass.

- [ ] **Step 5: Commit the capacity planner**

```bash
git add src/placegame/inventory/fingerprint.py src/placegame/inventory/warehouse.py src/placegame/inventory/planner.py tests/inventory/test_planner.py
git commit -m "feat: plan safe inventory capacity recovery"
```

### Task 4: Persist Immutable Plans and Validate Preview State

**Files:**
- Modify: `src/placegame/models.py`
- Create: `migrations/versions/002_inventory.py`
- Modify: `src/placegame/inventory/planner.py`
- Create: `src/placegame/inventory/service.py`
- Test: `tests/inventory/test_execution.py`

**Interfaces:**
- Produces `InventoryService.list`, `sort`/`sort_locked`, `plan_cleanup`/`plan_cleanup_locked`, `get_cleanup_plan`, and `execute_cleanup`/`execute_cleanup_locked`; plan rows contain account, expiry, policy version, fingerprint, selected IDs/transfers, exclusions, expected freed slots, preview rewards, and confirmation class.

- [ ] **Step 1: Write failing stale-plan and expiry tests**

```python
async def test_scheduled_plan_expires_after_two_minutes(service, fake_clock):
    plan = await service.plan_cleanup(ACCOUNT_ID, "scheduled", actor=scheduler_actor)
    fake_clock.advance(minutes=2, seconds=1)
    with pytest.raises(PlanExpired):
        await service.execute_cleanup(ACCOUNT_ID, plan.plan_id, False, actor=scheduler_actor)

async def test_fingerprint_or_policy_change_rejects_execution(service, account_repo):
    plan = await service.plan_cleanup(ACCOUNT_ID, "manual_safe", actor=admin_actor)
    await account_repo.bump_policy_version(ACCOUNT_ID)
    with pytest.raises(StalePlan):
        await service.execute_cleanup(ACCOUNT_ID, plan.plan_id, False, actor=admin_actor)

async def test_sort_verifies_asset_identity_and_capacity(service, fake_game, audit):
    result = await service.sort(ACCOUNT_ID, actor=admin_actor)
    assert result.snapshot.asset_identity_multiset() == fake_game.inventory.asset_identity_multiset()
    assert await audit.has_verified("inventory_sort", ACCOUNT_ID)
```

- [ ] **Step 2: Run tests and verify missing persistence/validation failure**

Run: `uv run pytest tests/inventory/test_execution.py -q`

Expected: FAIL because inventory plan tables and service methods do not exist.

- [ ] **Step 3: Add migration and immutable plan service**

Create `inventory_plans` with `plan_id`, `account_id`, `created_at`, `expires_at`, `policy_version`, `inventory_fingerprint`, JSON selected IDs/transfers/exclusions/rewards, `confirmation_required`, `status`, and a unique `(plan_id, account_id)` index. Create `inventory_alerts` for pressure level, reason, acknowledged timestamp, and correlation ID.

`plan_cleanup` refreshes the authoritative bag/equipment/warehouse/catalog/codex state, classifies every asset, calls `decompose_preview` once for the selected explicit IDs, rejects unexpected reward types, and persists that exact preview with a two-minute scheduled or five-minute manual expiry:

```python
async def list(self, account_id: UUID, filters: InventoryFilters, *, actor: Actor) -> InventorySnapshot:
    async with self.accounts.locked(account_id) as locked:
        snapshot = await cast(InventoryGameApi, locked.api).inventory_snapshot()
        return snapshot.filtered(filters)

async def sort(self, account_id: UUID, *, actor: Actor) -> VerifiedSortResult:
    async with self.accounts.locked(account_id) as locked:
        return await self.sort_locked(locked, actor=actor)

async def sort_locked(self, locked: LockedAccount, *, actor: Actor) -> VerifiedSortResult:
    api = cast(InventoryGameApi, locked.api)
    before = await api.inventory_snapshot()
    response = await api.sort()
    after = await api.inventory_snapshot()
    verified = before.asset_identity_multiset() == after.asset_identity_multiset() and before.capacity == after.capacity
    if not verified:
        await self.alerts.pause_for_inconsistent_counts(locked.account_id)
        raise InventoryStateMismatch("sort changed asset identity or capacity")
    await self.audit.record(actor, locked.account_id, "inventory_sort", verified=True)
    return VerifiedSortResult(response=response, snapshot=after)

async def plan_cleanup(self, account_id: UUID, mode: CleanupMode, *, actor: Actor) -> CleanupPlan:
    async with self.accounts.locked(account_id) as locked:
        return await self.plan_cleanup_locked(locked, mode, actor=actor)

async def plan_cleanup_locked(self, locked: LockedAccount, mode: CleanupMode, *, actor: Actor) -> CleanupPlan:
    api = cast(InventoryGameApi, locked.api)
    snapshot = await api.inventory_snapshot()
    if mode == "scheduled" and capacity_pressure(snapshot, locked.policy).level in {"warning", "critical"}:
        snapshot = (await self.sort_locked(locked, actor=actor)).snapshot
    draft = build_cleanup_draft(snapshot, locked.policy, mode, self.clock.now())
    preview = await api.decompose_preview(list(draft.equipment_ids)) if draft.equipment_ids else DecomposePreview.empty()
    self._require_expected_reward_types(preview)
    return await self.plans.create_cleanup(draft.with_preview(preview))
```

```python
async def get_cleanup_plan(self, account_id: UUID, plan_id: UUID, *, actor: Actor) -> CleanupPlan:
    await self.accounts.snapshot(account_id, actor=actor)
    return await self.plans.get_cleanup(plan_id, account_id)

async def execute_cleanup(self, account_id: UUID, plan_id: UUID, confirm: bool, *, actor: Actor) -> CleanupResult:
    async with self.accounts.locked(account_id) as locked:
        return await self.execute_cleanup_locked(locked, plan_id, confirm, actor=actor)

async def execute_cleanup_locked(self, locked: LockedAccount, plan_id: UUID, confirm: bool, *, actor: Actor) -> CleanupResult:
    api = cast(InventoryGameApi, locked.api)
    plan = await self.plans.get_cleanup_for_update(plan_id, locked.account_id)
    self._validate_expiry_and_confirmation(plan, confirm, actor)
    current = await api.inventory_snapshot()
    if inventory_fingerprint(current) != plan.inventory_fingerprint or locked.policy.version != plan.policy_version:
        raise StalePlan("inventory or policy changed; regenerate the plan")
    self._validate_selected_assets(current, plan)
    await self.plans.mark_executing(plan_id, plan.version)
    try:
        if plan.transfers:
            await self._execute_transfers_without_exceeding_capacity(api, plan.transfers)
        if plan.equipment_ids:
            preview = await api.decompose_preview(list(plan.equipment_ids))
            self._require_expected_preview(preview, plan.preview_rewards)
            await api.decompose(list(plan.equipment_ids))
        verified = await self._reconcile_counts(api, plan)
    except AmbiguousMutation:
        verified = await self._reconcile_counts(api, plan)
        if not verified:
            await self.alerts.pause_for_inconsistent_counts(locked.account_id)
            await self.plans.finish(plan_id, "unknown", {"verified": False})
            raise
    except Exception:
        await self.audit.high_severity(actor, locked.account_id, plan_id, "inventory_partial_or_rejected")
        await self.plans.finish(plan_id, "failed", {"verified": False})
        raise
    if not verified:
        await self.alerts.pause_for_inconsistent_counts(locked.account_id)
        await self.audit.high_severity(actor, locked.account_id, plan_id, "inventory_verification_failed")
        await self.plans.finish(plan_id, "unknown", {"verified": False})
        raise InventoryStateMismatch("post-action counts do not match the plan")
    await self.plans.finish(plan_id, "succeeded", {"verified": verified})
    return CleanupResult(plan_id=plan_id, verified=verified)
```

The service performs non-destructive sort separately, requires `confirm=True` and `inventory:confirm` for manual extended plans, rejects cross-account IDs, and refuses to continue after a partial response. It records preview, policy version, actor, correlation ID, and post-action counts in audit.

- [ ] **Step 4: Run migration and execution tests**

Run: `uv run pytest tests/integration/test_migrations.py tests/inventory/test_execution.py -q`

Expected: the Testcontainers migration reaches the inventory head; stale, expired, cross-account, preview-mismatch, timeout, and verified-success tests pass.

- [ ] **Step 5: Commit the plan/execution checkpoint**

```bash
git add src/placegame/models.py migrations/versions/002_inventory.py src/placegame/inventory/planner.py src/placegame/inventory/service.py tests/inventory/test_execution.py
git commit -m "feat: add immutable inventory cleanup plans"
```

### Task 5: Add Warehouse Transfers and Equipment Replacement Suggestions

**Files:**
- Modify: `src/placegame/inventory/warehouse.py`
- Create: `src/placegame/inventory/replacements.py`
- Modify: `src/placegame/inventory/service.py`
- Test: `tests/inventory/test_replacements.py`

**Interfaces:**
- Produces `warehouse_transfer`, `plan_replacement`/`execute_replacement`, `plan_item_use`/`execute_item_use`, `plan_equipment_action`/`execute_equipment_action`, and `plan_recycle`/`execute_recycle`; every plan is account-bound and `ReplacementPlan` never performs a wear operation.

- [ ] **Step 1: Write failing replacement and confirmation tests**

```python
async def test_positive_score_candidate_still_requires_confirmation(service):
    plan = await service.plan_replacement(ACCOUNT_ID, "eq-upgrade", actor=admin_actor)
    assert plan.score_delta > 0
    assert plan.confirmation_required is True
    with pytest.raises(ConfirmationRequired):
        await service.execute_replacement(ACCOUNT_ID, plan.plan_id, confirm=False, actor=admin_actor)

async def test_withdrawal_is_never_automatic(service, scheduler_actor):
    plan = await service.warehouse_transfer(ACCOUNT_ID, "withdraw", AssetRef("item", "mat-1"), 1, confirm=False, actor=scheduler_actor)
    assert plan.confirmation_required is True
    with pytest.raises(ScopeRequired):
        await service.warehouse_transfer(ACCOUNT_ID, "withdraw", AssetRef("item", "mat-1"), 1, confirm=True, actor=scheduler_actor)

async def test_low_risk_deposit_is_still_plan_backed(service, scheduler_actor):
    first = await service.warehouse_transfer(ACCOUNT_ID, "deposit", AssetRef("item", "excess-mat"), 80, confirm=False, actor=scheduler_actor)
    assert isinstance(first, TransferPlan)
    second = await service.warehouse_transfer(ACCOUNT_ID, "deposit", AssetRef("item", "excess-mat"), 80, confirm=False, actor=scheduler_actor)
    assert isinstance(second, TransferResult) and second.verified

async def test_item_use_requires_the_original_confirmed_plan(service, fake_game):
    plan = await service.plan_item_use(ACCOUNT_ID, "choice-box-1", actor=admin_actor)
    with pytest.raises(ConfirmationRequired):
        await service.execute_item_use(ACCOUNT_ID, plan.plan_id, confirm=False, actor=admin_actor)
    result = await service.execute_item_use(ACCOUNT_ID, plan.plan_id, confirm=True, actor=confirmed_admin_actor)
    assert result.verified and fake_game.last_request.json_body == {"itemId": "choice-box-1"}

@pytest.mark.parametrize("action", ["take_off", "unlock"])
async def test_equipment_action_and_protected_recycle_require_confirmation(service, action):
    plan = await service.plan_equipment_action(ACCOUNT_ID, "eq-1", action, actor=admin_actor)
    with pytest.raises(ConfirmationRequired):
        await service.execute_equipment_action(ACCOUNT_ID, plan.plan_id, confirm=False, actor=admin_actor)
    recycle = await service.plan_recycle(ACCOUNT_ID, "protected-item", 1, actor=admin_actor)
    with pytest.raises(ConfirmationRequired):
        await service.execute_recycle(ACCOUNT_ID, recycle.plan_id, confirm=False, actor=admin_actor)
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/inventory/test_replacements.py -q`

Expected: FAIL because replacement comparison and confirmation gates are absent.

- [ ] **Step 3: Implement comparison and plan-backed transfer gates**

`plan_replacement` returns current/candidate summaries, score delta, base/extra attribute deltas, bind/lock state, rare-affix changes, loadout impact, and whether the old item remains protected. It never calls `wear`. Warehouse deposits use the allowlist and reserves from Task 3; withdrawals and high-value assets always create a five-minute confirmation plan, reject quantity above current state, and never support first-release high-value batches. Replacement, item-use, equipment-action, and recycle plans use the inherited core `PlanStore.create/get_for_update/mark_executing/finish` lifecycle; transfer plans use the inventory store methods frozen above. Forbidden operation names are rejected before any game call.

```python
async def warehouse_transfer(self, account_id: UUID, direction: Direction, asset: AssetRef, quantity: int, confirm: bool, *, actor: Actor) -> TransferPlan | TransferResult:
    async with self.accounts.locked(account_id) as locked:
        api = cast(InventoryGameApi, locked.api)
        snapshot = await api.inventory_snapshot()
        current = snapshot.require_asset(asset)
        if quantity != current.amount:
            raise InventoryError("partial_transfer_unsupported")
        plan = await self.plans.find_exact_pending_transfer(account_id, actor, direction, asset, quantity)
        created = plan is None
        if plan is None:
            plan = await self.plans.create_transfer(snapshot, locked.policy, actor, direction, current)
        if created:
            return plan
        if plan.confirmation_required and not confirm:
            return plan
        if plan.confirmation_required:
            self.permissions.require_confirmation(actor, confirm, scope="inventory:confirm")
        await self._revalidate_transfer_plan(plan, current, locked.policy)
        if direction == "deposit":
            self._require_deposit_allowlist_and_reserves(current, locked.policy)
            response = await api.warehouse_deposit(asset.entry_type, asset.entry_id)
        else:
            response = await api.warehouse_withdraw(asset.entry_type, asset.entry_id)
        return await self._verify_transfer(api, current, direction, response)
```

```python
def compare_replacement(current: EquipmentSummary, candidate: EquipmentSummary) -> ReplacementPlanDraft:
    return ReplacementPlanDraft(
        current=current.safe_summary(), candidate=candidate.safe_summary(),
        score_delta=candidate.score - current.score,
        base_attribute_delta=attribute_delta(current.base_attributes, candidate.base_attributes),
        extra_attribute_delta=attribute_delta(current.extra_attributes, candidate.extra_attributes),
        rare_affix_changes=affix_delta(current.affixes, candidate.affixes),
        old_item_remains_protected=True, confirmation_required=True,
    )
```

`execute_replacement` requires the original five-minute plan, `confirm=True`, and confirmation permission; it rechecks account ownership, candidate/current IDs, lock/bind/affix/score state and policy version, calls `wear` once, refreshes the equipped slot, and audits the verified change. `plan_equipment_action` accepts only `take_off` or `unlock`, and execution dispatches to `take_off` or `toggle_lock` only after proving the current state still matches the plan. Protected recycle uses the exact stored item ID/amount and repeats `recycle_preview` before one `recycle` call. Consumable use and container opening use `plan_item_use`/`execute_item_use`, call the typed `/api/inventory/use-item` method exactly once, and reconcile the item stack plus resulting rewards before reporting success. All these actions use the same five-minute confirmation protocol. Unbind, quality upgrade, reforge, enhance, inherit, and market operations have no service method.

- [ ] **Step 4: Run replacement and warehouse tests**

Run: `uv run pytest tests/inventory/test_replacements.py -q`

Expected: positive-score, bind/lock, forbidden-operation, withdrawal, take-off, unlock, protected-recycle, item-use, and confirmation tests pass.

- [ ] **Step 5: Commit the transfer/replacement checkpoint**

```bash
git add src/placegame/inventory/warehouse.py src/placegame/inventory/replacements.py src/placegame/inventory/service.py tests/inventory/test_replacements.py
git commit -m "feat: gate warehouse withdrawal and equipment replacement"
```

### Task 6: Integrate Inventory With Scheduler and MCP

**Files:**
- Create: `src/placegame/inventory/mcp.py`
- Modify: `src/placegame/mcp/tools.py`
- Modify: `src/placegame/app.py`
- Modify: `src/placegame/jobs/handlers.py`
- Test: `tests/inventory/test_mcp.py`
- Test: `tests/inventory/test_acceptance.py`

**Interfaces:**
- Registers and advertises `inventory_list`, `inventory_cleanup_plan`, `inventory_cleanup_execute`, `warehouse_transfer`, `equipment_replacement_plan`, and `equipment_replacement_execute` with the core MCP adapter. It extends `TOOL_SCOPES` for the two equipment-replacement names and injects `InventoryPressureHook`, which implements the core `InventorySafetyPort`, into every reward-generating scheduler handler.

- [ ] **Step 1: Write failing MCP, threshold, and isolation tests**

```python
async def test_inventory_tools_require_operation_scope_and_confirm_scope(mcp_client, operate_token):
    plan = await mcp_client.call("inventory_cleanup_plan", {"account_id": str(ACCOUNT_ID), "mode": "manual_extended"}, token=operate_token)
    assert plan.json["confirmation_required"] is True
    denied = await mcp_client.call("inventory_cleanup_execute", {"account_id": str(ACCOUNT_ID), "plan_id": plan.json["plan_id"], "confirm": True}, token=operate_token)
    assert denied.error.code == "scope_required"

async def test_critical_pressure_blocks_boss_and_preserves_rules(core_stack):
    await core_stack.set_inventory_pressure(ACCOUNT_ID, used=96, capacity=100)
    outcome = await core_stack.run_personal_boss(ACCOUNT_ID)
    assert outcome.skipped_reason == "inventory_pressure_critical"
    assert core_stack.decompose_qualities(ACCOUNT_ID) <= {"white", "green", "blue"}
    assert core_stack.next_capacity_retry(ACCOUNT_ID) == timedelta(minutes=5)

async def test_asset_ids_cannot_cross_accounts(core_stack):
    with pytest.raises(AssetNotFound):
        await core_stack.inventory.execute_cleanup(ACCOUNT_B, PLAN_FROM_ACCOUNT_A, False, actor=scheduler_actor)

async def test_batch_high_value_inventory_execution_is_rejected(mcp_client, confirm_token):
    denied = await mcp_client.call("equipment_replacement_execute", {"account_ids": [str(ACCOUNT_ID), str(ACCOUNT_B)], "plans": {}}, token=confirm_token)
    assert denied.error.code == "batch_confirmation_unsupported"
```

- [ ] **Step 2: Run integration tests and verify missing registrations**

Run: `uv run pytest tests/inventory/test_mcp.py tests/inventory/test_acceptance.py -q`

Expected: FAIL until the tools and scheduler hook are registered.

- [ ] **Step 3: Implement scope gates and pressure hook**

Each MCP handler validates exactly one `AccountTarget` selector and checks `game:read`/`game:operate`. Read/list, cleanup planning, and low-risk batch execution return a per-account result map; asset- or plan-bound batch execution supplies an explicit per-account payload map, and one account's failure does not undo another account. Manual extended execution, withdrawal, replacement execution, and purple+ decomposition require `inventory:confirm` and reject any multi-account target with `batch_confirmation_unsupported`. Unlock, protected recycling, take-off, and consumable use remain WebUI-only actions in the first release and still require the same service-level confirmation permission. MCP results return selected and excluded assets with protection reasons, expected freed slots, preview rewards, expiry, and correlation ID; raw IDs are detail fields only.

```python
async def inventory_cleanup_execute(args: CleanupExecuteInput, ctx: McpContext) -> dict:
    actor, account_id = await ctx.authorize_one(args.account_id, scope="game:operate")
    plan = await ctx.inventory.get_cleanup_plan(account_id, args.plan_id, actor=actor)
    if plan.confirmation_required:
        ctx.require_scope(actor, "inventory:confirm")
    result = await ctx.inventory.execute_cleanup(account_id, plan.plan_id, args.confirm, actor=actor)
    return redact(result.model_dump(mode="json"))

async def before_reward_generating_action(self, locked: LockedAccount, kind: Literal["idle", "boss", "profession", "reward"], *, actor: Actor) -> PressureDecision:
    api = cast(InventoryGameApi, locked.api)
    snapshot = await api.inventory_snapshot()
    pressure = capacity_pressure(snapshot, locked.policy)
    if pressure.level in {"warning", "critical"}:
        plan = await self.inventory.plan_cleanup_locked(locked, "scheduled", actor=actor)  # includes verified sorting
        await self.inventory.execute_cleanup_locked(locked, plan.plan_id, False, actor=actor)
        snapshot = await api.inventory_snapshot()
    if pressure.level == "critical" and snapshot.free_slots < 10:
        await self.alerts.raise_persistent(locked.account_id, "inventory_pressure_critical")
        await self.jobs.retry_in(locked.account_id, "inventory_capacity", timedelta(minutes=5))
        return PressureDecision(allow=(kind == "idle" and snapshot.can_accept_idle_rewards), reason="inventory_pressure_critical")
    return PressureDecision(allow=True)
```

In `create_app`, construct one `InventoryPressureHook` and pass it as the core `InventorySafetyPort` dependency instead of `UnavailableInventorySafety`; register inventory handlers through the MCP extension registry only after the service is fully constructed. Add `equipment_replacement_plan: game:operate` and `equipment_replacement_execute: game:operate` to `TOOL_SCOPES`; execution separately requires `inventory:confirm`. The hook runs under the same account lock before idle collection, every boss handler, profession settlement, and safe reward claims. At unresolved critical pressure it always blocks bosses and other rewards that cannot fit, attempts idle collection only when the refreshed server state proves the reward fits, otherwise raises a persistent alert and retries capacity planning every five minutes. It never increases the quality ceiling or removes a protection reason.

- [ ] **Step 4: Run inventory acceptance and MCP tests**

Run: `uv run pytest tests/inventory -q`

Expected: all inventory tests pass, including property safety, stale plan, timeout reconciliation, threshold behavior, scope separation, and multi-account isolation.

- [ ] **Step 5: Commit the integrated inventory module**

```bash
git add src/placegame/inventory src/placegame/mcp/tools.py src/placegame/app.py src/placegame/jobs/handlers.py tests/inventory
git commit -m "feat: integrate protected inventory automation"
```

## Inventory Self-Review Checklist

- Spec coverage: Tasks 1–2 cover every listed API, all seven qualities/statuses, every protection reason, and forbidden automatic classes; Task 3 covers exact capacity thresholds, target space, material/profession reserves, and warehouse consolidation; Task 4 covers fresh listing, immutable plans, fingerprints, expiry, preview, atomic execution, conflicts, timeouts, and audit; Task 5 covers replacement suggestions plus confirmation-only wear, take-off, unlock, withdrawal, recycle, and item use; Task 6 covers MCP scopes, core safety-port injection, scheduler pressure blocking, alerts, and multi-account isolation.
- Placeholder scan command: `rg -n -i "T[O]DO|T[B]D|F[I]XME|implement[ ]later|fill[ ]in|write[ ]tests[ ]for[ ]the[ ]above|appropriate[ ]error[ ]handling|similar[ ]to[ ]task" docs/superpowers/plans/2026-08-17-placegame-inventory.md`; expected output is empty.
- Type/signature check: `uv run pyright src/placegame/inventory src/placegame/game tests/inventory` must report zero errors; `InventoryService`, `InventoryPlanStore`, `CleanupPlan`, `InventoryGameApi`, and the core `AccountService`/`InventorySafetyPort` signatures above must match exactly.
- Fresh verification: `uv run pytest tests/integration/test_migrations.py tests/inventory -q` and `docker compose config` must succeed before handing the module to WebUI work.
