# PlaceGame Idle Contract Status

Status: `live_contract_unverified`

The bootstrap, idle summary, and idle collection fixtures in
`tests/fixtures/game/v1` are synthetic, schema-minimum documents. They are
not captures of the live game contract.

P2 may test MCP behavior against the fake game server, but it must not expose
live `idle_collect`. That requires an opt-in credentialed capture that has been
redacted with `placegame.security.redaction.redact` and reviewed first.
