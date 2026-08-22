# PlaceGame Idle Contract Status

Status: `live_contract_unverified`

The fixtures in `tests/fixtures/game/v1` hold synthetic values. They are not
captures of live account state, and no end-to-end behaviour has been verified
against the live game, so `verified_at` stays `null` on every document.

## What has been verified: response shape only

On 2026-08-22 the response *shape* of two read-only endpoints was checked
against a redacted HAR capture of the live web client, cross-read against the
official CLI bundle (`placegame-cli.mjs`, which ships unminified). The
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
(`min(policy, capacity)`) no longer has a server-authoritative bound. It falls
back to `IDLE_CAPACITY_SECONDS`, which mirrors the 480-minute ceiling in the
game's own web bundle. The safety property is kept, but if the game changes that
ceiling we will not learn about it from the API. A server-sent `capacitySeconds`
still wins if one ever appears.

## Still unverified

`/api/battle/idle-collect` has no capture at all: its `shape_verified_at` is
`null` and `IdleCollectResult.collected` remains a guess. It is the one operation
that mutates game state, so it stays registered only under `TEST_MODE` and must
not be exposed live. Doing so requires an opt-in credentialed capture, redacted
with `placegame.security.redaction.redact` and reviewed first.
