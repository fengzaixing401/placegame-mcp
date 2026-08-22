# PlaceGame Idle Contract Status

Status: `live_contract_unverified`

The status stays unverified because it gates one thing: exposing a production
mutation. No mutation has been exercised. Read-only calls, however, are now
verified against the live game (below), and the fixtures in
`tests/fixtures/game/v1` still hold synthetic values rather than captures of
live account state.

## Verified end-to-end: the read-only path

On 2026-08-22, with the account owner's explicit authorisation and on the
understanding that the password would be rotated afterwards, this client was run
against the live game with real credentials. Five operations succeeded through
the production code path:

| Operation | Result |
| --- | --- |
| `login` | 43-character `sessionToken`, `expiresAt` in **milliseconds** |
| `bootstrap` | identity read from envelope-level `user.id` (21 chars) |
| `idle_summary` | `validSeconds` float, e.g. 38333.707 |
| `catalog` | `qualities` / `jobs` / `items` |
| `view_sections` | 22 bosses with a 64-char section etag |

Reaching login at all required declaring `x-placegame-client-version`; without
it every endpoint answers 426. The same request returns 426 without the header
and 401 with it, so the header is a gate, not an optimisation.

No mutation has been exercised, so `verified_at` stays `null` on every fixture
and `idle_collect` remains unproven.

## Shape cross-check

The response *shape* was first checked against a redacted HAR capture of the
live web client, cross-read against the official CLI bundle
(`placegame-cli.mjs`, which ships unminified). The
`shape_verified_at` and `shape_source` fields record this per fixture. Verifying
a shape is weaker than verifying the contract: it says the field names and types
we parse match one observed response, not that the semantics are understood or
that the server will keep answering that way.

That check corrected four things that had been guessed wrong:

- login returns `data.sessionToken` (plus `data.expiresAt`), not `data.token`
- bootstrap reports identity as envelope-level `user.id`; there is no
  `data.accountId`
- idle summary reports `data.validSeconds` as a float; there is no
  `accumulatedSeconds`, and no capacity field at all
- catalog carries `qualities`/`jobs`/`items` and no version field

Two envelope rules were also wrong, and both produced a misleading
`game_contract_changed`:

- a business refusal arrives as HTTP 200 with `{"ok": false, "error": ...}`, so a
  2xx status alone does not mean success
- mutating endpoints answer with `data = {"result": ..., "statePatch": ...}`, so
  the operation's own payload is `data.result`

## Idle capacity is now our assumption, not the server's

The live idle summary reports no capacity, so the planner's threshold ceiling
(`min(policy, capacity)`) has no server-authoritative bound and is skipped: the
threshold is whatever the operator's policy says.

An earlier attempt defaulted the ceiling to 480 minutes, mirroring a constant in
the game's web bundle. An authorised live login disproved that: the account read
back `validSeconds = 37760` (about 10.5 hours), well past 28800. A guessed cap
does not merely lose information here, it corrupts the decision — every account
above the guess would report `collect` regardless of policy. The field name is
also the server's own answer to what the cap is for: `validSeconds` is the time
the game already counts as collectible, so clamping it again client-side has
nothing to add. A server-sent `capacitySeconds` still clamps if one appears.

## Still unverified

`/api/battle/idle-collect` has no capture at all: its `shape_verified_at` is
`null` and `IdleCollectResult.collected` remains a guess. It is the one operation
that mutates game state, so it stays registered only under `TEST_MODE` and must
not be exposed live. Doing so requires an opt-in credentialed capture, redacted
with `placegame.security.redaction.redact` and reviewed first.
