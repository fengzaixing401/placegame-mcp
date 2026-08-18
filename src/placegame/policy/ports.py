from typing import Protocol
from uuid import UUID

from placegame.contracts import Actor

from .models import AccountPolicy, VersionedPolicy


class ServerIdleCapacityReader(Protocol):
    async def __call__(self, account_id: UUID) -> int: ...


class PolicyService(Protocol):
    async def get(self, account_id: UUID) -> VersionedPolicy: ...

    async def save(
        self, account_id: UUID, policy: AccountPolicy, expected_version: int, *, actor: Actor
    ) -> VersionedPolicy: ...

    async def server_idle_capacity(self, account_id: UUID) -> int: ...
