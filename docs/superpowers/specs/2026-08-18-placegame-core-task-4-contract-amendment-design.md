# PlaceGame Core Task 4 Contract Amendment

**Status:** Approved 2026-08-18

**Scope:** Account policy dependency and ambiguous-mutation verification only

**Parent design:** `2026-08-17-placegame-mcp-core-design.md`

## Context

Core Task 4 must produce the frozen `LockedAccount` and `AccountService.mutate`
interfaces before Core Task 5 creates the concrete `VersionedPolicy` type and
policy service. The original verifier signature also required a response value
even when a mutation timed out after being sent and therefore produced no
response value. These two ordering conflicts must be resolved without inventing
policy data or hiding an absent mutation response behind an unsafe cast.

## Approved Decisions

### Consumer-owned policy port

Task 4 defines a small, account-scoped `PolicyProvider` protocol consumed by
`AccountService`. Its `get(account_id)` operation returns a forward-referenced
`VersionedPolicy`. Task 4 does not create a concrete policy model, policy JSON,
or default policy. It must not import the future policy implementation at
runtime.

`AccountService` receives the provider by dependency injection. Its production
default is a fail-closed provider that raises the stable `PolicyUnavailable`
error. Task 4 tests inject a typed stub provider. Core Task 5 creates the real
`VersionedPolicy` and policy service, then wires that service into the existing
port without changing `LockedAccount` or its callers. Any type-only forward
declaration used during Task 4 must keep Task 4 static checks green and be
replaced by the concrete Task 5 import when that type exists.

This keeps ownership aligned with the dependency direction: the account layer
owns the port it needs, while the later policy layer supplies the adapter.

### Honest verifier input

The frozen verifier contract is amended to:

```python
verify: Callable[[GameApi, T | None], Awaitable[bool]] | None = None
```

A normal response passes the actual `T`. An ambiguous outcome with no response
passes `None`. `None` has exactly one meaning: the request may have reached the
game server, but no response value exists. Implementations must not use
`cast(T, None)`, a sentinel pretending to be a game response, or a fabricated
response model.

Because a successfully reconciled timeout still has no response object,
`MutationOutcome[T]` stores `result: T | None` and separately records whether
the action was applied and reconciled. Callers must use those explicit fields
instead of inferring application from result presence.

## Mutation Data Flow

For each mutation, the service performs the following sequence while holding
only that account's transaction-scoped PostgreSQL advisory lock:

1. Reload the managed account and reject disabled, paused, or removed accounts.
2. Ensure the account session and refresh the minimum authoritative game state.
3. Resolve that account's policy through `PolicyProvider`; an unavailable
   provider stops the operation before the game mutation is sent.
4. Recheck plan and policy preconditions.
5. Invoke the typed game mutation once for the current attempt.
6. On a response, refresh authoritative state and call `verify(api, result)`
   when a verifier was supplied.
7. On an ambiguous transport outcome, refresh authoritative state and call
   `verify(api, None)`. A true result produces an applied, reconciled outcome
   with no response value. A false result, verifier failure, or absent verifier
   produces an unresolved/deferred outcome and never blindly repeats the
   mutation.
8. A definite game-state conflict follows the existing bounded conflict path:
   refresh and recheck before each retry, add jitter, and retry no more than two
   times. This conflict path is distinct from an ambiguous transport outcome.
9. Persist a redacted audit event and release the transaction and lock.

The public `ensure_session` entry point acquires the same account lock when it
runs independently. `mutate` and `locked` call one internal lock-aware session
routine with their existing transaction; they must not open a nested account
transaction or reacquire the lock through a second database session.

No code path acquires a second account lock while holding the first. Policy,
session, snapshot, retry, and audit state is keyed by the same `account_id` so a
failure or pause cannot affect another account.

## Error Handling

- Missing real policy wiring fails with `PolicyUnavailable` before any game
  write.
- A token-only account that needs renewal pauses only itself with
  `session_refresh_required`.
- A verifier exception is treated as unresolved reconciliation; it is audited
  with sanitized metadata and does not authorize another mutation attempt.
- An ambiguous outcome without a verifier is unresolved and deferred, not
  guessed as success or failure.
- Only definite conflict responses use the two-retry conflict budget.
- Credentials, tokens, verifier internals, and raw game bodies never enter the
  audit result.

## Verification Requirements

Task 4 tests must demonstrate all of the following:

- the default policy provider fails closed before the typed mutation is called;
- an injected provider returns only the requested account's policy;
- a normal response supplies the real response object to the verifier;
- a commit-then-timeout supplies `None`, reconciles as applied, returns no
  fabricated result, and calls the mutation exactly once;
- an unresolved or verifier-less ambiguous outcome calls the mutation exactly
  once;
- conflict retries refresh state before each retry and stop after two retries;
- disabling, pausing, renewing, or failing one account does not change another
  account's credentials, state, policy, plan, or job state;
- PostgreSQL advisory locking serializes same-account mutations while allowing
  different accounts to progress independently; and
- the amended public annotation and `MutationOutcome[T].result: T | None`
  remain visible to static analysis.

The focused unit and PostgreSQL integration suites run only on the isolated
Singapore test stack, with unique resource names, no published database port,
and exact cleanup after verification.
