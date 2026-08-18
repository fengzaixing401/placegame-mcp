from __future__ import annotations

import asyncio
import hashlib
import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Generic, Literal, Protocol, TypeVar
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from placegame.contracts import Actor
from placegame.errors import (
    AccountDisabled,
    AccountNotFound,
    AccountPaused,
    AccountRemoved,
    AccountError,
    AmbiguousMutation,
    AuthenticationRequired,
    GameConflict,
    GameError,
    GameUnavailable,
    PlanPreconditionFailed,
    PolicyUnavailable,
    ReconciliationRequired,
    SessionRejected,
)
from placegame.game.client import GameApi
from placegame.models import ActionPlan, GameAccount
from placegame.security.crypto import SecretBox

from .locks import account_lock
from .reconcile import TokenExpiryResolver, default_token_expiry
from .repository import AccountRepository


T = TypeVar("T")
Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
Jitter = Callable[[], float]


if TYPE_CHECKING:
    class VersionedPolicy(Protocol):
        version: int


class PolicyProvider(Protocol):
    async def get(self, account_id: UUID) -> VersionedPolicy: ...


class GameApiFactory(Protocol):
    def __call__(self, session_token: str | None) -> GameApi: ...


class FailClosedPolicyProvider:
    async def get(self, account_id: UUID) -> VersionedPolicy:
        raise PolicyUnavailable()


@dataclass(frozen=True)
class ManagedAccount:
    id: UUID
    label: str
    auth_mode: Literal["credentials", "token_only"]
    enabled: bool
    paused_reason: str | None
    session_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SessionState:
    account_id: UUID
    authenticated: bool
    refreshed: bool
    expires_at: datetime | None
    paused_reason: str | None


@dataclass(frozen=True)
class RemovalReceipt:
    account_id: UUID
    removed_at: datetime
    disabled_job_count: int


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: UUID
    enabled: bool
    paused_reason: str | None
    authenticated: bool
    session_expires_at: datetime | None
    state: Mapping[str, object]
    state_fingerprint: str
    fetched_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class LockedAccount:
    account_id: UUID
    api: GameApi
    policy: VersionedPolicy
    snapshot: AccountSnapshot


@dataclass(frozen=True)
class MutationOutcome(Generic[T]):
    applied: bool
    reconciled: bool
    result: T | None


@dataclass(frozen=True)
class _SessionResolution:
    state: SessionState
    api: GameApi | None
    bootstrap_account_id: str | None


_RENEWAL_WINDOW = timedelta(hours=24)
_AUTH_FAILURE_WINDOW = timedelta(hours=1)
_SNAPSHOT_TTL = timedelta(minutes=5)
_AUTH_RETRY_DELAYS = (1.0, 2.0)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_actor(actor: Actor) -> tuple[str, str]:
    return f"{actor.kind}:{actor.actor_id}", actor.kind


class AccountService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        secret_box: SecretBox,
        game_factory: GameApiFactory,
        *,
        policy_provider: PolicyProvider | None = None,
        token_expiry: TokenExpiryResolver = default_token_expiry,
        clock: Clock = _utc_now,
        sleeper: Sleeper = asyncio.sleep,
        jitter: Jitter = random.random,
        repository: AccountRepository | None = None,
    ) -> None:
        self.sessions = sessions
        self.secret_box = secret_box
        self.game_factory = game_factory
        self.policy_provider = policy_provider or FailClosedPolicyProvider()
        self.token_expiry = token_expiry
        self.clock = clock
        self.sleeper = sleeper
        self.jitter = jitter
        self.repository = repository or AccountRepository()

    async def add_credentials(
        self, label: str, username: str, password: str, *, actor: Actor
    ) -> ManagedAccount:
        normalized = self._normalize_label(label)
        token, expiry, _api, bootstrap_id = await self._login_and_bootstrap(
            username, password
        )
        now = self.clock()
        async with self.sessions.begin() as session:
            record = GameAccount(label=normalized, auth_mode="credentials", enabled=True)
            record.set_game_username(username, self.secret_box)
            record.set_password(password, self.secret_box)
            record.set_session_token(token, self.secret_box)
            record.session_expires_at = expiry
            record.last_success_at = now
            session.add(record)
            await session.flush()
            await self._audit(
                session,
                actor=actor,
                account_id=record.id,
                action="account.add_credentials",
                result={"status": "succeeded", "bootstrap": bool(bootstrap_id)},
            )
            return self._managed(record)

    async def add_token_only(
        self, label: str, session_token: str, *, actor: Actor
    ) -> ManagedAccount:
        normalized = self._normalize_label(label)
        if not isinstance(session_token, str) or not session_token:
            raise ValueError("session token is required")
        try:
            api = self.game_factory(session_token)
            bootstrap = await api.bootstrap()
        except Exception:
            raise AuthenticationRequired() from None
        now = self.clock()
        async with self.sessions.begin() as session:
            record = GameAccount(label=normalized, auth_mode="token_only", enabled=True)
            record.set_session_token(session_token, self.secret_box)
            record.session_expires_at = self.token_expiry(session_token)
            record.last_success_at = now
            session.add(record)
            await session.flush()
            await self._audit(
                session,
                actor=actor,
                account_id=record.id,
                action="account.add_token_only",
                result={"status": "succeeded", "bootstrap": bool(bootstrap.account_id)},
            )
            return self._managed(record)

    async def update_label(
        self, account_id: UUID, label: str, *, actor: Actor
    ) -> ManagedAccount:
        normalized = self._normalize_label(label)
        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                record = await self._require(session, account_id)
                self._reject_removed(record)
                before = {"label": record.label}
                record.label = normalized
                await self._audit(
                    session,
                    actor=actor,
                    account_id=account_id,
                    action="account.label.update",
                    before=before,
                    after={"label": normalized},
                    result={"status": "succeeded"},
                )
                return self._managed(record)

    async def update_credentials(
        self,
        account_id: UUID,
        username: str | None,
        password: str,
        *,
        actor: Actor,
    ) -> ManagedAccount:
        if not isinstance(password, str) or not password:
            raise ValueError("password is required")
        pending: AccountError | None = None
        result: ManagedAccount | None = None
        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                record = await self._require(session, account_id)
                self._reject_removed(record)
                current_username = record.get_game_username(self.secret_box)
                proposed_username = username or current_username
                verified: tuple[str, datetime | None, GameApi, str] | None = None
                if proposed_username:
                    try:
                        verified = await self._login_and_bootstrap(
                            proposed_username, password
                        )
                    except AccountError:
                        pass
                if verified is None or proposed_username is None:
                    pending = AuthenticationRequired()
                else:
                    token, expiry, _api, _bootstrap = verified
                    record.auth_mode = "credentials"
                    record.set_game_username(proposed_username, self.secret_box)
                    record.set_password(password, self.secret_box)
                    record.set_session_token(token, self.secret_box)
                    record.session_expires_at = expiry
                    record.paused_reason = None
                    record.auth_failure_count = 0
                    record.auth_failure_window_started_at = None
                    record.last_success_at = self.clock()
                    await self._audit(
                        session,
                        actor=actor,
                        account_id=account_id,
                        action="account.credentials.update",
                        result={"status": "succeeded"},
                    )
                    result = self._managed(record)
                if pending is not None:
                    await self._audit(
                        session,
                        actor=actor,
                        account_id=account_id,
                        action="account.credentials.update",
                        result={"status": "rejected"},
                    )
        if pending is not None:
            raise pending from None
        assert result is not None
        return result

    async def update_token_only(
        self, account_id: UUID, session_token: str, *, actor: Actor
    ) -> ManagedAccount:
        if not isinstance(session_token, str) or not session_token:
            raise ValueError("session token is required")
        pending: AccountError | None = None
        result: ManagedAccount | None = None
        bootstrap_id: str | None = None
        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                record = await self._require(session, account_id)
                self._reject_removed(record)
                try:
                    api = self.game_factory(session_token)
                    bootstrap = await api.bootstrap()
                    bootstrap_id = bootstrap.account_id
                except Exception:
                    pending = AuthenticationRequired()
                if pending is None:
                    record.auth_mode = "token_only"
                    record.set_game_username(None, self.secret_box)
                    record.set_password(None, self.secret_box)
                    record.set_session_token(session_token, self.secret_box)
                    record.session_expires_at = self.token_expiry(session_token)
                    record.paused_reason = None
                    record.auth_failure_count = 0
                    record.auth_failure_window_started_at = None
                    record.last_success_at = self.clock()
                    await self._audit(
                        session,
                        actor=actor,
                        account_id=account_id,
                        action="account.token.update",
                        result={"status": "succeeded", "bootstrap": bool(bootstrap_id)},
                    )
                    result = self._managed(record)
                else:
                    await self._audit(
                        session,
                        actor=actor,
                        account_id=account_id,
                        action="account.token.update",
                        result={"status": "rejected"},
                    )
        if pending is not None:
            raise pending from None
        assert result is not None
        return result

    async def enable(self, account_id: UUID, *, actor: Actor) -> None:
        await self._set_lifecycle(account_id, actor=actor, action="enable", enabled=True)

    async def disable(self, account_id: UUID, *, actor: Actor) -> None:
        await self._set_lifecycle(account_id, actor=actor, action="disable", enabled=False)

    async def pause(self, account_id: UUID, reason: str, *, actor: Actor) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("pause reason is required")
        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                record = await self._require(session, account_id)
                self._reject_removed(record)
                record.paused_reason = reason.strip()
                await self._audit(
                    session,
                    actor=actor,
                    account_id=account_id,
                    action="account.pause",
                    result={"status": "paused"},
                )

    async def resume(self, account_id: UUID, *, actor: Actor) -> None:
        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                record = await self._require(session, account_id)
                self._reject_removed(record)
                record.paused_reason = None
                await self._audit(
                    session,
                    actor=actor,
                    account_id=account_id,
                    action="account.resume",
                    result={"status": "resumed"},
                )

    async def disable_drain_remove(
        self, account_id: UUID, *, actor: Actor
    ) -> RemovalReceipt:
        # Flip the admission flag in its own short transaction first. Existing
        # work may finish, while every new mutation observes disabled state.
        async with self.sessions.begin() as session:
            record = await self._require(session, account_id)
            if record.paused_reason == "removed":
                return RemovalReceipt(account_id, self.clock(), 0)
            record.enabled = False
            await session.flush()

        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                record = await self._require(session, account_id)
                disabled_jobs = await self.repository.disable_jobs(session, account_id)
                record.enabled = False
                record.paused_reason = "removed"
                record.set_game_username(None, self.secret_box)
                record.set_password(None, self.secret_box)
                record.set_session_token(None, self.secret_box)
                record.session_expires_at = None
                await self._audit(
                    session,
                    actor=actor,
                    account_id=account_id,
                    action="account.remove",
                    result={"status": "removed", "disabled_jobs": disabled_jobs},
                )
                return RemovalReceipt(account_id, self.clock(), disabled_jobs)

    async def get(self, account_id: UUID) -> ManagedAccount:
        async with self.sessions() as session:
            record = await self._require(session, account_id)
            return self._managed(record)

    async def ensure_session(
        self, account_id: UUID, *, actor: Actor
    ) -> SessionState:
        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                record = await self._require(session, account_id)
                resolution = await self._ensure_locked(session, record, actor)
                return resolution.state

    def locked(
        self, account_id: UUID
    ) -> AbstractAsyncContextManager[LockedAccount]:
        return self._locked(account_id)

    @asynccontextmanager
    async def _locked(self, account_id: UUID) -> AsyncIterator[LockedAccount]:
        pending: AccountError | None = None
        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                record = await self._require(session, account_id)
                self._require_mutable(record)
                resolution = await self._ensure_locked(
                    session, record, Actor("scheduler", "locked-context")
                )
                if resolution.api is None or resolution.bootstrap_account_id is None:
                    pending = AuthenticationRequired()
                else:
                    snapshot = await self._save_snapshot(
                        session, record, resolution, authenticated=True
                    )
                    policy = await self.policy_provider.get(account_id)
                    yield LockedAccount(account_id, resolution.api, policy, snapshot)
        if pending is not None:
            raise pending from None

    async def snapshot(self, account_id: UUID, *, actor: Actor) -> AccountSnapshot:
        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                record = await self._require(session, account_id)
                resolution = await self._ensure_locked(session, record, actor)
                return await self._save_snapshot(
                    session,
                    record,
                    resolution,
                    authenticated=resolution.api is not None,
                )

    async def mutate(
        self,
        account_id: UUID,
        operation: Callable[[GameApi], Awaitable[T]],
        *,
        actor: Actor,
        plan_id: UUID | None = None,
        verify: Callable[[GameApi, T | None], Awaitable[bool]] | None = None,
    ) -> MutationOutcome[T]:
        pending: Exception | None = None
        outcome: MutationOutcome[T] | None = None
        validated_plan_id: UUID | None = None
        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                try:
                    for attempt in range(3):
                        record = await self._require(session, account_id)
                        self._require_mutable(record)
                        resolution = await self._ensure_locked(session, record, actor)
                        if resolution.api is None or resolution.bootstrap_account_id is None:
                            raise AuthenticationRequired() from None
                        policy = await self.policy_provider.get(account_id)
                        del policy  # The provider call is the precondition boundary in Task 4.
                        await self._check_plan(session, plan_id, account_id)
                        validated_plan_id = plan_id
                        api = resolution.api
                        try:
                            value = await operation(api)
                        except GameConflict:
                            if attempt >= 2:
                                raise
                            try:
                                await api.bootstrap()
                            except GameError:
                                raise
                            await self.sleeper(max(0.0, self.jitter()))
                            continue
                        except AmbiguousMutation:
                            if verify is None:
                                raise ReconciliationRequired() from None
                            try:
                                await api.bootstrap()
                                verified = await verify(api, None)
                            except Exception:
                                raise ReconciliationRequired() from None
                            if not verified:
                                raise ReconciliationRequired() from None
                            record.last_success_at = self.clock()
                            outcome = MutationOutcome(True, True, None)
                            await self._audit(
                                session,
                                actor=actor,
                                account_id=account_id,
                                action="account.mutate",
                                plan_id=validated_plan_id,
                                result={"status": "succeeded", "reconciled": True},
                            )
                            break
                        except SessionRejected:
                            raise AuthenticationRequired() from None
                        try:
                            await api.bootstrap()
                        except SessionRejected:
                            raise AuthenticationRequired() from None
                        if verify is not None:
                            try:
                                verified = await verify(api, value)
                            except Exception:
                                raise ReconciliationRequired() from None
                            if not verified:
                                raise ReconciliationRequired() from None
                        record.last_success_at = self.clock()
                        outcome = MutationOutcome(True, False, value)
                        await self._audit(
                            session,
                            actor=actor,
                            account_id=account_id,
                            action="account.mutate",
                            plan_id=validated_plan_id,
                            result={"status": "succeeded", "reconciled": False},
                        )
                        break
                except Exception as exc:
                    pending = exc if isinstance(exc, (AccountError, GameError)) else ReconciliationRequired()
                    await self._audit(
                        session,
                        actor=actor,
                        account_id=account_id,
                        action="account.mutate",
                        plan_id=validated_plan_id,
                        result={"status": "failed", "error": type(pending).__name__},
                    )
        if pending is not None:
            raise pending from None
        assert outcome is not None
        return outcome

    async def _set_lifecycle(
        self,
        account_id: UUID,
        *,
        actor: Actor,
        action: str,
        enabled: bool,
    ) -> None:
        async with self.sessions.begin() as session:
            async with account_lock(session, account_id):
                record = await self._require(session, account_id)
                self._reject_removed(record)
                record.enabled = enabled
                await self._audit(
                    session,
                    actor=actor,
                    account_id=account_id,
                    action=f"account.{action}",
                    result={"status": action + "d" if action == "disable" else action + "d"},
                )

    async def _login_and_bootstrap(
        self, username: str, password: str
    ) -> tuple[str, datetime | None, GameApi, str]:
        for attempt in range(3):
            try:
                login_api = self.game_factory(None)
                login = await login_api.login(username, password)
                token = login.token
                api = self.game_factory(token)
                bootstrap = await api.bootstrap()
                return token, self.token_expiry(token), api, bootstrap.account_id
            except (SessionRejected, AmbiguousMutation, GameUnavailable):
                if attempt < 2:
                    await self.sleeper(_AUTH_RETRY_DELAYS[attempt])
        raise AuthenticationRequired() from None

    async def _ensure_locked(
        self, session: AsyncSession, record: GameAccount, actor: Actor
    ) -> _SessionResolution:
        now = self.clock()
        paused = record.paused_reason
        if paused is not None:
            return _SessionResolution(
                SessionState(record.id, False, False, record.session_expires_at, paused),
                None,
                None,
            )
        try:
            token = record.get_session_token(self.secret_box)
            expiry = record.session_expires_at
            needs_refresh = token is None or (
                expiry is not None and expiry <= now + _RENEWAL_WINDOW
            )
            if record.auth_mode == "token_only":
                if needs_refresh:
                    return await self._pause_for_refresh(session, record, actor)
                api = self.game_factory(token)
                try:
                    bootstrap = await api.bootstrap()
                except SessionRejected:
                    return await self._pause_for_refresh(session, record, actor)
                record.last_success_at = now
                return _SessionResolution(
                    SessionState(record.id, True, False, expiry, None),
                    api,
                    bootstrap.account_id,
                )

            if not needs_refresh and token is not None:
                api = self.game_factory(token)
                try:
                    bootstrap = await api.bootstrap()
                    record.last_success_at = now
                    return _SessionResolution(
                        SessionState(record.id, True, False, expiry, None),
                        api,
                        bootstrap.account_id,
                    )
                except SessionRejected:
                    pass

            username = record.get_game_username(self.secret_box)
            password = record.get_password(self.secret_box)
            if not username or not password:
                raise AuthenticationRequired() from None
            new_token, new_expiry, api, bootstrap_id = await self._login_and_bootstrap(
                username, password
            )
            record.set_session_token(new_token, self.secret_box)
            record.session_expires_at = new_expiry
            record.auth_failure_count = 0
            record.auth_failure_window_started_at = None
            record.last_success_at = now
            return _SessionResolution(
                SessionState(record.id, True, True, new_expiry, None),
                api,
                bootstrap_id,
            )
        except AuthenticationRequired:
            return await self._record_auth_failure(session, record, actor)

    async def _pause_for_refresh(
        self, session: AsyncSession, record: GameAccount, actor: Actor
    ) -> _SessionResolution:
        record.paused_reason = "session_refresh_required"
        record.last_error_at = self.clock()
        await self._audit(
            session,
            actor=actor,
            account_id=record.id,
            action="account.session.refresh_required",
            result={"status": "paused", "severity": "critical"},
        )
        return _SessionResolution(
            SessionState(record.id, False, False, record.session_expires_at, record.paused_reason),
            None,
            None,
        )

    async def _record_auth_failure(
        self, session: AsyncSession, record: GameAccount, actor: Actor
    ) -> _SessionResolution:
        now = self.clock()
        started = record.auth_failure_window_started_at
        if started is None or now - started >= _AUTH_FAILURE_WINDOW:
            record.auth_failure_window_started_at = now
            record.auth_failure_count = 1
        else:
            record.auth_failure_count += 1
        record.last_error_at = now
        if record.auth_failure_count >= 3:
            record.paused_reason = "authentication_required"
        await self._audit(
            session,
            actor=actor,
            account_id=record.id,
            action="account.session.refresh_failed",
            result={
                "status": "paused" if record.paused_reason else "failed",
                "severity": "critical" if record.paused_reason else "warning",
            },
        )
        return _SessionResolution(
            SessionState(record.id, False, False, record.session_expires_at, record.paused_reason),
            None,
            None,
        )

    async def _save_snapshot(
        self,
        session: AsyncSession,
        record: GameAccount,
        resolution: _SessionResolution,
        *,
        authenticated: bool,
    ) -> AccountSnapshot:
        state: dict[str, object] = {}
        if resolution.bootstrap_account_id is not None and authenticated:
            state = {"accountId": resolution.bootstrap_account_id}
        fetched = self.clock()
        expires = fetched + _SNAPSHOT_TTL
        fingerprint = hashlib.sha256(
            json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        await self.repository.save_snapshot(
            session,
            account_id=record.id,
            state=state,
            state_fingerprint=fingerprint,
            fetched_at=fetched,
            expires_at=expires,
        )
        return AccountSnapshot(
            account_id=record.id,
            enabled=record.enabled,
            paused_reason=record.paused_reason,
            authenticated=authenticated,
            session_expires_at=record.session_expires_at,
            state=state,
            state_fingerprint=fingerprint,
            fetched_at=fetched,
            expires_at=expires,
        )

    async def _check_plan(
        self, session: AsyncSession, plan_id: UUID | None, account_id: UUID
    ) -> None:
        if plan_id is None:
            return
        plan = await session.get(ActionPlan, plan_id)
        if (
            plan is None
            or plan.account_id != account_id
            or plan.expires_at <= self.clock()
            or plan.execution_state not in {"pending", "confirmed"}
            or (plan.confirmation_required and plan.confirmed_at is None)
        ):
            raise PlanPreconditionFailed() from None

    async def _audit(
        self,
        session: AsyncSession,
        *,
        actor: Actor,
        account_id: UUID | None,
        action: str,
        result: Mapping[str, object] | None = None,
        before: Mapping[str, object] | None = None,
        after: Mapping[str, object] | None = None,
        plan_id: UUID | None = None,
    ) -> None:
        actor_value, source = _safe_actor(actor)
        await self.repository.add_audit(
            session,
            actor=actor_value,
            source=source,
            account_id=account_id,
            action=action,
            result=result,
            before=before,
            after=after,
            plan_id=plan_id,
        )

    @staticmethod
    def _normalize_label(label: str) -> str:
        if not isinstance(label, str):
            raise ValueError("label is required")
        normalized = label.strip()
        if not 1 <= len(normalized) <= 120:
            raise ValueError("label must be between 1 and 120 characters")
        return normalized

    @staticmethod
    async def _require(session: AsyncSession, account_id: UUID) -> GameAccount:
        record = await session.get(GameAccount, account_id)
        if record is None:
            raise AccountNotFound() from None
        return record

    @staticmethod
    def _reject_removed(record: GameAccount) -> None:
        if record.paused_reason == "removed":
            raise AccountRemoved() from None

    @classmethod
    def _require_mutable(cls, record: GameAccount) -> None:
        cls._reject_removed(record)
        if not record.enabled:
            raise AccountDisabled() from None
        if record.paused_reason:
            raise AccountPaused() from None

    @staticmethod
    def _managed(record: GameAccount) -> ManagedAccount:
        auth_mode: Literal["credentials", "token_only"] = (
            "credentials" if record.auth_mode == "credentials" else "token_only"
        )
        return ManagedAccount(
            id=record.id,
            label=record.label,
            auth_mode=auth_mode,
            enabled=record.enabled,
            paused_reason=record.paused_reason,
            session_expires_at=record.session_expires_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


__all__ = [
    "AccountService",
    "AccountSnapshot",
    "FailClosedPolicyProvider",
    "GameApiFactory",
    "LockedAccount",
    "ManagedAccount",
    "MutationOutcome",
    "PolicyProvider",
    "RemovalReceipt",
    "SessionState",
    "default_token_expiry",
]
