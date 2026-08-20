from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from placegame.contracts import Actor
from placegame.errors import PolicyUnavailable
from placegame.models import AccountPolicy as AccountPolicyRow
from placegame.models import AuditEvent, GameAccount
from placegame.policy.models import AccountPolicy, PolicyConflict, VersionedPolicy
from tests.unit.test_accounts import account_database_url, account_env


ADMIN = Actor("webui", "admin")


def test_account_policy_defaults_and_rejects_unknown_or_invalid_values():
    assert AccountPolicy().idle_threshold_minutes == 690
    with pytest.raises(ValidationError):
        AccountPolicy.model_validate({"unexpected": True})
    with pytest.raises(ValidationError):
        AccountPolicy.model_validate(
            {"inventory_warning_percent": 96, "inventory_critical_percent": 95}
        )


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
        session.add(
            AccountPolicyRow(
                account_id=account.id,
                policy={"idle_threshold_minutes": "bad"},
                policy_version=1,
            )
        )
    with pytest.raises(PolicyUnavailable):
        await account_env.policy.get(account.id)


async def test_save_is_exact_cas_updates_both_versions_and_audits_safely(account_env):
    account, _ = await account_env.add_token()
    saved = await account_env.policy.save(
        account.id, AccountPolicy(material_reserve=80), 1, actor=ADMIN
    )
    assert saved.version == 2 and saved.material_reserve == 80
    async with account_env.sessions() as session:
        row = await session.get(GameAccount, account.id)
        policy_row = await session.get(AccountPolicyRow, account.id)
        assert row is not None and policy_row is not None
        assert row.policy_version == policy_row.policy_version == 2
        event = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "policy.save")
            )
        ).one()
        assert event.result == {"status": "saved", "version": 2}


async def test_stale_save_preserves_document_and_both_versions(account_env):
    account, _ = await account_env.add_token()
    await account_env.policy.save(
        account.id, AccountPolicy(material_reserve=80), 1, actor=ADMIN
    )
    with pytest.raises(PolicyConflict):
        await account_env.policy.save(
            account.id, AccountPolicy(material_reserve=99), 1, actor=ADMIN
        )
    current = await account_env.policy.get(account.id)
    assert current.version == 2 and current.material_reserve == 80


async def test_concurrent_policy_saves_have_exactly_one_winner(account_env):
    account, _ = await account_env.add_token()
    results = await asyncio.gather(
        account_env.policy.save(
            account.id, AccountPolicy(material_reserve=70), 1, actor=ADMIN
        ),
        account_env.policy.save(
            account.id, AccountPolicy(material_reserve=71), 1, actor=ADMIN
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(value, VersionedPolicy) for value in results) == 1
    assert sum(isinstance(value, PolicyConflict) for value in results) == 1


async def test_server_idle_capacity_delegates_once_without_nested_policy_lock(account_env):
    account, _ = await account_env.add_token()
    calls: list[UUID] = []
    account_env.policy.capacity_reader = (
        lambda account_id: calls.append(account_id) or 43200
    )
    assert await account_env.policy.server_idle_capacity(account.id) == 43200
    assert calls == [account.id]
