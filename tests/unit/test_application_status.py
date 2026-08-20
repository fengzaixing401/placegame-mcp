from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from placegame.accounts.service import AccountSnapshot


class Accounts:
    def __init__(self, snapshot: AccountSnapshot) -> None:
        self.snapshot_value = snapshot

    async def get(self, account_id):
        return SimpleNamespace(
            id=account_id,
            label="alpha",
            enabled=True,
            paused_reason=None,
        )

    async def snapshot(self, account_id, *, actor):
        return self.snapshot_value


async def test_status_query_returns_only_safe_authoritative_state():
    from placegame.application.models import AccountStatus
    from placegame.application.status import AccountStatusQuery
    from placegame.contracts import Actor

    account_id = uuid4()
    snapshot = AccountSnapshot(
        account_id=account_id,
        enabled=True,
        paused_reason=None,
        authenticated=True,
        session_expires_at=None,
        state={
            "accountId": "game-a",
            "idle": {"accumulatedSeconds": 3600, "capacitySeconds": 7200},
        },
        state_fingerprint="snapshot",
        fetched_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
    )

    result = await AccountStatusQuery(Accounts(snapshot)).get(account_id, actor=Actor("webui", "local"))

    assert isinstance(result, AccountStatus)
    assert result.account.auth_state == "authenticated"
    assert result.bootstrap_account_id == "game-a"
    assert result.idle.accumulated_seconds == 3600
    assert "token" not in repr(result).lower()


async def test_status_query_converts_invalid_snapshot_to_safe_contract_error():
    from placegame.application.errors import ApplicationError
    from placegame.application.status import AccountStatusQuery
    from placegame.contracts import Actor

    account_id = uuid4()
    invalid = AccountSnapshot(
        account_id=account_id,
        enabled=True,
        paused_reason=None,
        authenticated=True,
        session_expires_at=None,
        state={"accountId": "game-a", "idle": {"capacitySeconds": 7200}},
        state_fingerprint="snapshot",
        fetched_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ApplicationError, match="game_contract_changed"):
        await AccountStatusQuery(Accounts(invalid)).get(account_id, actor=Actor("webui", "local"))
