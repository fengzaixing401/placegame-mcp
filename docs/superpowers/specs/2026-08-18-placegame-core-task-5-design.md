# PlaceGame Core Task 5 Policy and Plan Design

**Status:** Approved architecture and domain rules on 2026-08-18; written-spec review pending

**Parent plan:** `2026-08-17-placegame-mcp-core`

**Related contract:** `2026-08-18-placegame-core-plan-fingerprint-contract-amendment-design`

## Goal

Implement strict per-account policies, durable action plans, and deterministic
decisions for idle collection, personal and ordinary bosses, world-boss
assistance, professions, and the fixed safe reward claims. Every plan must use
the same canonical state projection when it is created and executed.

## Scope and Decomposition

Core Task 5 is delivered as three independently reviewed checkpoints:

1. **5A Policy foundation:** strict domain policy models, policy persistence,
   default handling, and optimistic versioned updates.
2. **5B Plan foundation:** typed action decisions, canonical fingerprints,
   plan persistence and terminal execution states.
3. **5C Domain decisions:** typed state adapters, idle/boss/profession/reward
   planners, bounded boss optimization, and operation-specific resolvers.

The checkpoints share one final Core Task 5 milestone. Task 6 consumes these
services and does not duplicate policy decisions, plan parsing, or fingerprint
construction.

## Architecture

`PolicyService` is the concrete `PolicyProvider` required by
`AccountService`. It reads the existing `account_policies` JSONB row and the
matching `game_accounts.policy_version`. A missing row represents the version-1
default policy until a policy is explicitly saved. A corrupt row, missing
account, or disagreement between the two versions fails closed as
`PolicyUnavailable`.

Policy writes use one PostgreSQL transaction with the account advisory lock and
account row lock. `expected_version` is compared against both persisted
versions; the successful write increments both to `expected_version + 1`.
Concurrent writers therefore have exactly one winner. The existing monotonic
account trigger remains the database backstop.

`PlanStore` owns typed plan serialization and lifecycle validation. JSONB is an
implementation detail: every plan is validated through a discriminated action
union before persistence and after reload. A plan contains one action family
only. Mixed-family plans are rejected. Task 4 does not inspect
`proposed_actions`.

`PolicyEngine`, `BossOptimizer`, and `SafeRewardPlanner` consume normalized,
typed state. They never make arbitrary HTTP requests and never persist raw
responses. A trusted domain executor maps a reloaded plan family to a fixed
typed operation and the matching `StateFingerprintResolver`, then calls
`AccountService.mutate`.

The game client gains explicit response models for every field used by these
decisions. A missing required field or an unrecognized critical response shape
raises `GameSchemaMismatch`; the planner never guesses from untyped
`model_extra` data. Raw resolver state is held only in memory for the current
attempt.

## Policy Foundation (5A)

### Domain model

`src/placegame/policy/models.py` defines strict Pydantic models with
`extra="forbid"`:

- `AccountPolicy`: idle threshold, boss chance and paid-attempt policy, world
  collaboration, material reserve, profession targets/horizon, inventory
  thresholds and protection rules, and safe-reward enablement.
- `VersionedPolicy`: frozen `AccountPolicy` plus `version >= 1`.
- Typed policy errors and update results contain no secrets or raw game data.

The defaults are the values in the Core plan: idle threshold 690 minutes,
minimum boss chance 80, paid personal attempts disabled, world attempts 3,
material reserve 64, food target 6, potion target 12, profession horizon 12
hours, inventory warning/critical thresholds 85/95, blue maximum automatic
quality, and safe claims enabled.

### Persistence contract

`src/placegame/policy/store.py` provides the concrete implementation used by
`PolicyService`:

```python
class PolicyService(Protocol):
    async def get(self, account_id: UUID) -> VersionedPolicy: ...
    async def save(
        self,
        account_id: UUID,
        policy: AccountPolicy,
        expected_version: int,
        *,
        actor: Actor,
    ) -> VersionedPolicy: ...
    async def server_idle_capacity(self, account_id: UUID) -> int: ...
```

`get` returns the virtual default at version 1 when no policy row exists. It
does not silently create a row. `save` validates the account, locks the row,
checks both versions, writes the validated JSON document and increments both
versions atomically. A stale expected version raises a stable policy conflict
without changing either row. `server_idle_capacity` is a read-only account
operation that uses the account service's authenticated, locked state path; it
does not open a second nested account lock.

### Policy tests

5A tests cover strict unknown-field and range rejection, default-row behavior,
malformed-row fail-closed behavior, version CAS races, account/policy version
consistency, and the fact that a policy update invalidates an existing plan.

## Plan Foundation (5B)

### Typed actions

`src/placegame/policy/plans.py` defines:

- `ActionFamily = Literal["idle", "personal_boss", "ordinary_boss", "world_boss", "profession", "safe_reward"]`;
- typed selected/skipped/blocked decisions with a required stable reason;
- fixed action payloads for the registered game methods only;
- sanitized estimated costs and risk classes; and
- `TypedActionPlan`, which contains one family, policy version, expiry,
  decisions, and its opaque fingerprint.

All action payloads are converted to JSON using explicit aliases and validated
again when loaded from `ActionPlan.proposed_actions`. Arbitrary endpoint names,
URLs, request bodies, claim-all actions, and specialization selection are not
representable.

### Canonical fingerprint

`canonical_fingerprint` serializes a normalized projection with sorted object
keys, deterministic keyed-array ordering, UTF-8 JSON, and no whitespace. The
stored value is exactly:

```text
pgfp:v1:<64 lowercase hexadecimal SHA-256 characters>
```

It is always nonempty and fits the existing 128-character column. The prefix
is the projection version; changing a projection requires a new prefix.

The projections are:

- idle: accumulated and capacity seconds;
- personal boss: all evaluated entries, attempt pool, availability, refresh
  state, selected configuration, materials, equipment, potion/affix state, and
  final preview;
- ordinary boss: type, ordinary attempts, blocked/refresh/difficulty state,
  selected configuration, and final preview;
- world boss: every evaluated instance's key, lifecycle state, active/alive
  state, and attempt counters;
- profession: specialization, queue, unlock/progress, evaluated actions and
  recipes, material/output balances, and recipe version; and
- safe rewards: every evaluated candidate's kind, identifier, completion and
  claim state, choice count, cost, and overflow decision.

Policy fields are not part of the projection because Task 4 checks the policy
version independently. Beijing wall-clock eligibility is also not part of the
world-state projection.

### Plan lifecycle

The valid terminal states are `executed`, `failed`, and
`reconciliation_required`. A confirmation-required plan must pass through
`confirmed` before execution. A successful or positively reconciled mutation
transitions to `executed`; a rejected precondition or deterministic decision
transitions to `failed`; an ambiguous or cancellation-after-send outcome
transitions to `reconciliation_required`. No terminal plan returns to
`pending`.

The account mutation transaction performs the terminal transition together
with its sanitized audit. The account advisory lock serializes same-account
plan execution, so a sequential replay observes the terminal state before a
second typed request. The plan layer does not expose resolver callbacks or
raw state through MCP/WebUI configuration.

### Plan tests

5B tests cover deterministic fingerprint ordering, projection-version changes,
typed JSON round-trips, mixed-family rejection, expiry and confirmation,
terminal transitions, safe audit references, sequential replay prevention,
and policy-version invalidation. Each supported family has a creation/execution
test that proves both paths produce the same projection.

## Domain Decisions (5C)

### Idle

Build the threshold as the lower of the policy threshold and server capacity.
The operation is selected only when accumulated time reaches that threshold.
The idle resolver reads a fresh typed `IdleSummary`; idle collection is the
only registered mutation. Emergency retry timing remains a scheduler concern
owned by Task 6.

### Bosses

`BossOptimizer` generates exactly three bounded skill candidates (output,
survival, balanced), previews at most 12 no-affix combinations, keeps the best
three by predicted result, chance, player HP, boss HP, and deterministic tie
order, then previews at most 12 affix combinations. Material boost is chosen
only after combat configuration and only on hard/nightmare when the reserve of
64 remains. The optimizer may use an already active potion when it respects the
policy reserve and the fight is not easy. Because no typed combat-potion equip
endpoint has been observed or registered, a configuration that requires a
potion change is recorded as `blocked`; Task 5 never guesses an endpoint or
misuses `/api/professions/supply/equip` for combat supplies.

Personal bosses use the free shared pool and evaluate nightmare, hard, normal.
Ordinary bosses accept map entries and world entries with explicit solo
attempts. World assistance uses only `boss_assist`; each attempt is rechecked
using `myAttemptCount` and `remainingAttemptCount`.

### Professions

Read and preserve `selectedProfessionKey`. Settlement and queue refills are
planned around the five-entry maximum, two-entry/six-hour refill thresholds,
12-hour horizon, unlock milestones, configured food/potion stock, and required
inputs. No planner can represent a specialization-select operation.

### Safe rewards

Only the five fixed claim operations are representable. Completed no-choice,
no-cost, non-overflowing candidates are selected. Choice, cost, overflow,
unknown, and inventory-safety-unavailable candidates are skipped or blocked
with a stable reason and never claimed automatically.

`UnavailableInventorySafety` remains the fail-closed default until the
Inventory plan installs its implementation.

### Typed state adapters

5C adds explicit models and adapters for the boss, profession, reward,
equipment, material, and inventory fields consumed by the planners. The
adapters may accept harmless forward-compatible fields, but every field used
for a decision is required and typed. A response that cannot be normalized
stops the affected plan with `GameSchemaMismatch` before any mutation.

### Domain tests

5C tests cover all policy defaults and boundaries, exact optimizer preview
limits and tie ordering, personal/ordinary/world selection, world attempt
rechecks, profession queue and stock priority, specialization immutability,
safe reward filtering, inventory safety blocking, no claim-all behavior,
typed schema mismatch propagation, and operation-specific resolver changes
invalidating plans before mutation.

## Error and Safety Rules

- `GameSchemaMismatch` from a typed read or resolver propagates unchanged.
- Invalid resolver output or a fingerprint mismatch is `PlanPreconditionFailed`.
- `PolicyUnavailable` and stale policy versions fail before a typed mutation.
- Unknown actions, unknown reward kinds, missing required fields, and mixed
  families fail closed; no best-effort fallback is allowed.
- Raw credentials, tokens, bodies, resolver state, verifier internals, and
  exception text never enter JSONB, audits, or public DTOs.
- All domain executors use the account-scoped `AccountService.mutate`; none
  bypasses the account lock or calls a generic HTTP method.

## File Boundaries

5A creates `src/placegame/policy/models.py`, `ports.py`, and `store.py`.
5B creates `src/placegame/policy/plans.py` and makes the narrow generic plan
terminal-transition change in `AccountService`.
5C creates `src/placegame/policy/engine.py`, `boss_optimizer.py`, and
`rewards.py`; it modifies only the typed game schemas/client/fake needed for
the normalized state adapters. No scheduler, MCP, WebUI, or Inventory
implementation belongs to Task 5.

## Verification Gates

Each checkpoint follows RED → GREEN on the Singapore OneSSH host using the
disposable runner and PostgreSQL constraints already binding Core Task 4.
No local pytest or Pyright, no browser automation, no real game endpoint, no
application image build, and no push are permitted. Each checkpoint gets a
focused review package; after 5C, the complete `Core 1-5` candidate receives a
whole-branch review before Task 6 begins.
