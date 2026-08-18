# PlaceGame Core Plan Fingerprint Contract Amendment

**Status:** Approved approach A on 2026-08-18; written-spec review pending

**Scope:** Operation-appropriate authoritative state preconditions only

**Parent design:** `2026-08-17-placegame-mcp-core-design.md`

**Related amendment:** `2026-08-18-placegame-core-task-4-contract-amendment-design.md`

## Context

Core Task 4 currently builds a diagnostic account snapshot from canonical
identity and idle-summary fields. It compares that snapshot fingerprint with
every `ActionPlan.state_fingerprint`, even though Core Task 5 plans boss,
profession, and reward operations from different authoritative state. Changes
in those domains can therefore leave a non-idle plan apparently valid.

The account snapshot remains useful for status and audit-safe diagnostics, but
the parent design explicitly says cached snapshots are not mutation
preconditions by themselves. Task 4 also cannot infer the required game reads
from Task 5's future `proposed_actions` JSON without taking ownership of policy
semantics that belong to Task 5.

## Approved Contract

Add one keyword-only callback to the frozen mutation interface:

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
) -> MutationOutcome[T]: ...
```

When `plan_id` is present, `state_fingerprint` is mandatory. Its absence, an
empty result, or a result longer than the persisted 128-character bound fails
closed with `PlanPreconditionFailed` before the mutation request starts. A
resolver supplied without a plan is not used because there is no persisted
fingerprint to compare.

The resolver is an internal trusted adapter, never an MCP or WebUI-supplied
callable. It receives only the already account-bound `GameApi`; callers cannot
supply a URL, path, arbitrary request body, or another account.

## Ownership

Task 4 owns orchestration:

1. reload the managed account and establish its authenticated session;
2. refresh the diagnostic account snapshot;
3. resolve the current account policy;
4. forcibly reload the plan for the current attempt;
5. invoke the resolver and compare its opaque result with the plan fingerprint;
6. start the typed mutation only after every precondition passes; and
7. repeat steps 1-5 before each bounded definite-conflict retry.

Task 5 owns the canonical domain projection and resolver implementation. The
same projection used to create an idle, boss, world-boss, profession, or reward
plan must be supplied at execution. Task 4 neither interprets
`proposed_actions` nor chooses view sections for those domains.

Raw domain state returned by resolver reads is not persisted or audited by
Task 4. Only the opaque fingerprint is compared. The existing sanitized
diagnostic snapshot remains separately persisted and does not replace this
comparison.

## Freshness And Errors

The plan must be reloaded from PostgreSQL on every attempt with identity-map
caching bypassed. Ownership is validated before its ID may be attached to an
audit event. Expiry, execution state, confirmation, policy version, and the
resolver result are all checked from that attempt's fresh values.

Typed game errors raised while the resolver performs authoritative reads retain
their existing stable classification because no mutation has been sent.
`GameSchemaMismatch` remains the contract-drift stop signal. Invalid resolver
output fails as `PlanPreconditionFailed`. Resolver internals, exception text,
raw game bodies, and raw state never enter audit results.

## Verification Requirements

Task 4 tests must demonstrate:

- a plan without a resolver is rejected before the typed mutation is called;
- matching policy and resolver fingerprints permit execution;
- empty and oversized resolver results fail closed;
- an independent transaction changing plan state between conflict attempts is
  observed on the next attempt;
- a resolver result changing between conflict attempts rejects the stale plan;
- representative boss, profession, and reward state changes invalidate their
  plans through injected resolvers without Task 4 interpreting those domains;
- resolver `GameSchemaMismatch` propagates unchanged before any write; and
- mutations without a plan preserve the existing callback-free contract.

Task 5 tests must later prove that plan creation and execution use the same
canonical projection for every supported action family.

## Non-Goals

- Do not make bootstrap extras the universal state contract.
- Do not issue a fixed broad `view_sections` request from Task 4.
- Do not add a constructor provider that parses Task 5 plan JSON.
- Do not expose the resolver through MCP, WebUI, or persisted configuration.
- Do not change the verifier or `MutationOutcome` amendments already approved.
