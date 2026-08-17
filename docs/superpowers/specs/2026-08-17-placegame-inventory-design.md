# PlaceGame Inventory and Warehouse Design

**Date:** 2026-08-17  
**Status:** Approved design, pending written-spec review  
**Parent specification:** `2026-08-17-placegame-mcp-core-design.md`

## 1. Goal

Keep every managed account able to collect idle rewards and boss drops without automatically destroying or consuming valuable assets. The chosen operating mode is hybrid: low-risk organization, transfer, and low-quality decomposition may run automatically; high-value, irreversible, or character-build decisions require explicit confirmation.

## 2. Supported Operations

### Read and plan

- List and filter bag equipment, item stacks, equipped gear, warehouse contents, capacities, locks, bind state, quality, score, affixes, and catalog metadata.
- Compare bag equipment with currently equipped gear by slot.
- Preview decomposition and recycle results through the game's preview endpoints.
- Produce a capacity-recovery plan with reasons and estimated freed slots.

### Low-risk automatic operations

- Sort inventory.
- Deposit policy-approved material stacks into warehouse while retaining a configured bag minimum.
- Decompose equipment only when it passes every automatic safety rule.
- Re-run a previously generated plan after state validation.

### Confirmation-required operations

- Wear or replace equipment.
- Take off equipment.
- Withdraw high-value assets from warehouse.
- Decompose purple, orange, red, or gold equipment.
- Use consumables or open containers.
- Unlock equipment.
- Recycle protected item types.

### Forbidden automatic operations

- Unbind equipment.
- Quality upgrade, reforge, enhance, or inherit.
- Market listing, sale, purchase, or price change.
- Any operation on an asset not returned by the current server state.

## 3. Game API Boundary

The module uses typed wrappers for the observed endpoints:

- `POST /api/inventory/sort`, with no body.
- `POST /api/equipment/decompose-preview`, with `{ equipmentIds: string[] }`.
- `POST /api/equipment/decompose`, with `{ equipmentIds: string[] }`.
- `POST /api/equipment/auto-decompose-rules`, with `{ patch: AutoDecomposeRulePatch }`; allowed patch fields are `autoRecycleQualities`, `keepScoreAbove`, `keepRareAffixes`, `autoRecycleMaxLevel`, and `autoRecycleProtectedStats`.
- `POST /api/equipment/auto-decompose`, with no body. Scheduled cleanup does not use this endpoint because it does not provide an immediate per-execution preview; the WebUI may expose it only as a confirmation-required action.
- `POST /api/inventory/recycle-preview`, with `{ itemId: string, amount: number }`.
- `POST /api/inventory/recycle`, with `{ itemId: string, amount: number }`.
- `POST /api/equipment/wear`, `POST /api/equipment/take-off`, and `POST /api/equipment/toggle-lock`, each with `{ equipmentId: string }`.
- `POST /api/warehouse/deposit` and `POST /api/warehouse/withdraw`, each with `{ entryType: "equipment" | "item", entryId: string }`.

The production service never exposes endpoint paths as user input. Scheduled decomposition always sends the explicit equipment IDs from a successful, current `decompose-preview` response.

## 4. Inventory Classification

Each asset receives zero or more protection reasons before a plan is built:

- `equipped`
- `locked`
- `quality_above_auto_limit`
- `score_upgrade_candidate`
- `rare_affix`
- `codex_missing`
- `profession_input_reserved`
- `boss_material_reserved`
- `configured_keep_item`
- `pending_plan_reference`
- `unknown_schema`

An asset with any protection reason is excluded from automatic decomposition. Protection reasons are shown in MCP and WebUI results.

## 5. Default Automatic Decomposition Rules

An equipment item may be automatically decomposed only when all conditions are true:

1. Status is `in_bag`.
2. It is neither equipped nor locked.
3. Quality is white, green, or blue.
4. Its score is not greater than the score of currently equipped gear in the same slot.
5. It has no rare or policy-protected affix.
6. It is not needed for a missing codex entry according to current codex data.
7. It is not referenced by an unexpired manual plan.
8. The game's decomposition preview succeeds and returns only expected rewards.

If a slot has no equipped item, all equipment for that slot is protected from automatic decomposition. If comparison data is incomplete, the item is protected as `unknown_schema`.

The policy may lower the automatic quality limit per account. Raising it above blue changes the action class to confirmation-required and cannot be made automatic in the first release.

## 6. Material and Item Transfer Rules

Warehouse deposits use an allowlist generated from item catalog types and account policy.

- Boss-difficulty materials retain at least 64 units in the bag by default.
- Profession inputs retain enough for queued work plus the configured stock-production horizon.
- Equipped food and boss potion stacks remain in the bag.
- Skill books, choice boxes, consumables, and unknown item types are not automatically transferred.
- A transfer never exceeds warehouse free capacity.
- The planner prefers consolidating an existing warehouse stack over creating a new stack when server data supports that distinction.

Automatic warehouse withdrawal is disabled. Agent or WebUI withdrawal requires a plan and confirmation.

## 7. Capacity Thresholds

Thresholds use occupied equipment entries plus item-stack entries exactly as the server counts bag usage.

### Below 85%

- No scheduled cleanup.
- Manual plans remain available.

### At or above 85%

1. Sort inventory.
2. Deposit approved excess material stacks.
3. Preview low-quality equipment decomposition.
4. Execute only if the plan passes automatic rules.
5. Target at least 20% free capacity or 15 free slots, whichever requires more space.

### At or above 95%

- Mark the account `inventory_pressure=critical`.
- Run the same safe sequence immediately.
- Temporarily block new boss challenges until at least ten slots are free.
- Attempt idle collection only after cleanup planning; if capacity cannot be made safe, preserve the non-destructive rule, raise a critical alert, and retry capacity planning every five minutes.

The emergency path never expands allowed qualities or removes a protection reason.

## 8. Plan-Before-Execute Protocol

Every cleanup execution uses an immutable plan containing:

- `plan_id`
- `account_id`
- creation and expiry time
- account policy version
- inventory fingerprint
- selected equipment IDs and item transfers
- protection decisions and exclusions
- expected freed slots and previewed rewards
- required confirmation class

Automatic plans expire after two minutes; manual plans expire after five minutes. Execution acquires the account lock, refreshes inventory, and rejects the plan if selected IDs, locks, qualities, scores, warehouse capacity, policy version, or preview rewards changed.

Partial execution is not allowed inside a single destructive plan. Non-destructive sorting may occur separately. If the game API applies only part of a mutation unexpectedly, the service refreshes state, records a high-severity audit event, and does not continue the remaining plan.

## 9. Equipment Upgrade Suggestions

The module may recommend an equipment replacement but never auto-wears it in hybrid mode.

A suggestion includes:

- current and candidate item summaries
- score delta
- base and extra attribute deltas
- bind state and lock state
- rare-affix changes
- loadout impact
- whether the old item would remain protected

The administrator or an MCP caller with confirmation permission must create and execute a separate wear plan. A positive score alone is not enough to bypass confirmation.

## 10. MCP Interface

- `inventory_list(account_id, location, filters)` is read-only.
- `inventory_cleanup_plan(account_id, mode)` returns a dry plan; `mode` is `scheduled`, `manual_safe`, or `manual_extended`.
- `inventory_cleanup_execute(account_id, plan_id, confirm)` executes a valid plan. `confirm=true` is required for manual plans.
- `warehouse_transfer(account_id, direction, asset, quantity, confirm)` is plan-backed.
- `equipment_replacement_plan(account_id, equipment_id)` produces a comparison.
- `equipment_replacement_execute(account_id, plan_id, confirm)` requires confirmation.

MCP tokens require `game:operate` for low-risk execution. Manual high-value actions additionally require `inventory:confirm`. Batch high-value inventory execution is not supported in the first release.

## 11. WebUI Behavior

- Bag and warehouse are separate tabs with quality, slot, score, lock, bind, protection, and text filters.
- Protected items show explicit badges and cannot be selected by automatic-cleanup controls.
- Cleanup starts with a preview page listing every selected and excluded asset.
- Confirmation-required operations show their irreversible effects and require a second confirmation action.
- The UI never displays raw game IDs as the primary label, but includes them in a copyable detail section for support.

## 12. Error Handling

- Preview failure prevents execution.
- Warehouse full changes deposits to skipped actions; it never triggers decomposition of extra quality tiers.
- Unknown quality or item type is protected.
- A server conflict invalidates the plan and asks the caller to regenerate it.
- If bag counts differ after a successful response, automation pauses for that account until a fresh snapshot is internally consistent.

## 13. Testing Strategy

- Property tests ensure an item with any protection reason is never auto-selected.
- Fixture tests cover all seven observed qualities and all equipment statuses.
- Plan-expiry and fingerprint-change tests prevent stale execution.
- Capacity tests cover bag and warehouse boundary values.
- Timeout reconciliation tests prevent double decomposition or transfer.
- Multi-account tests prove asset IDs from one account cannot be submitted to another.
- UI/API contract tests verify preview and confirmation requirements.

## 14. Acceptance Criteria

- Automatic cleanup cannot select equipped, locked, purple-or-higher, rare-affix, codex-missing, upgrade-candidate, reserved, or unknown assets.
- A stale plan cannot execute after any selected asset changes.
- Scheduled cleanup can recover capacity using only allowed deposits and low-quality decomposition.
- Failure to recover capacity produces an alert and blocks risky drop-generating work without broadening destruction rules.
- Every destructive result can be traced to its preview, policy version, actor, and verified post-action state.
