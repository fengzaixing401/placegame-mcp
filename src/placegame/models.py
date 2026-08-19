from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from placegame.contracts import EncryptedSecretFrame, encrypted_aad
from placegame.security.crypto import EncryptedSecret, SecretBox
from placegame.security.redaction import RedactedJSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class GameAccount(Base):
    __tablename__ = "game_accounts"
    __table_args__ = (
        CheckConstraint("policy_version >= 1", name="ck_game_accounts_policy_version"),
        UniqueConstraint("game_account_id", name="uq_game_accounts_game_account_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    game_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    _game_username: Mapped[EncryptedSecret | None] = mapped_column(
        "game_username", EncryptedSecretFrame(), nullable=True
    )
    auth_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    _password: Mapped[EncryptedSecret | None] = mapped_column(
        "password", EncryptedSecretFrame(), nullable=True
    )
    _session_token: Mapped[EncryptedSecret | None] = mapped_column(
        "session_token", EncryptedSecretFrame(), nullable=True
    )
    session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    paused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auth_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auth_failure_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __init__(self, **kwargs):
        if kwargs.get("id") is None:
            kwargs["id"] = uuid4()
        super().__init__(**kwargs)

    def set_game_username(self, value: str | None, secret_box: SecretBox) -> None:
        self._game_username = self._seal_secret(value, secret_box, "game_username")

    def get_game_username(self, secret_box: SecretBox) -> str | None:
        return self._open_secret(self._game_username, secret_box, "game_username")

    def set_password(self, value: str | None, secret_box: SecretBox) -> None:
        self._password = self._seal_secret(value, secret_box, "password")

    def get_password(self, secret_box: SecretBox) -> str | None:
        return self._open_secret(self._password, secret_box, "password")

    def set_session_token(self, value: str | None, secret_box: SecretBox) -> None:
        self._session_token = self._seal_secret(value, secret_box, "session_token")

    def get_session_token(self, secret_box: SecretBox) -> str | None:
        return self._open_secret(self._session_token, secret_box, "session_token")

    def _seal_secret(
        self, value: str | None, secret_box: SecretBox, column: str
    ) -> EncryptedSecret | None:
        if value is None:
            return None
        return secret_box.encrypt(value, aad=self._secret_aad(column))

    def _open_secret(
        self, value: EncryptedSecret | None, secret_box: SecretBox, column: str
    ) -> str | None:
        if value is None:
            return None
        return secret_box.decrypt(value, aad=self._secret_aad(column))

    def _secret_aad(self, column: str) -> str:
        return encrypted_aad("game_accounts", self.id, column)


class AccountPolicy(Base):
    __tablename__ = "account_policies"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(policy) = 'object'", name="ck_account_policies_policy_object"),
    )

    account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("game_accounts.id", ondelete="CASCADE"), primary_key=True)
    policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    __table_args__ = (
        CheckConstraint(
            "expires_at = fetched_at + INTERVAL '5 minutes'",
            name="ck_account_snapshots_snapshot_expiry",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("game_accounts.id", ondelete="CASCADE"), nullable=False)
    sanitized_state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("game_accounts.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    schedule: Mapped[str] = mapped_column(String(128), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    misfire_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (UniqueConstraint("account_id", "idempotency_key", name="uq_job_runs_account_idempotency"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("game_accounts.id", ondelete="CASCADE"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    dispatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionPlan(Base):
    __tablename__ = "action_plans"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("game_accounts.id", ondelete="CASCADE"), nullable=False)
    state_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    proposed_actions: Mapped[list] = mapped_column(JSONB, nullable=False)
    estimated_costs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    risk: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmation_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    execution_state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    execution_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class McpToken(Base):
    __tablename__ = "mcp_tokens"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False)
    account_allowlist: Mapped[list] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("game_accounts.id", ondelete="RESTRICT"), nullable=True)
    plan_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("action_plans.id", ondelete="RESTRICT"), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    costs: Mapped[dict | None] = mapped_column(RedactedJSON(), nullable=True)
    result: Mapped[dict | None] = mapped_column(RedactedJSON(), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before: Mapped[dict | None] = mapped_column(RedactedJSON(), nullable=True)
    after: Mapped[dict | None] = mapped_column(RedactedJSON(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class SchedulerLease(Base):
    __tablename__ = "scheduler_leases"
    __table_args__ = (
        CheckConstraint("name = 'default'", name="ck_scheduler_leases_default_name"),
    )

    name: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


Index("ix_account_policies_policy_version", AccountPolicy.policy_version)
Index("ix_account_snapshots_expires_at", AccountSnapshot.expires_at)
Index("ix_action_plans_expires_at", ActionPlan.expires_at)
Index("ix_jobs_due", Job.enabled, Job.next_run_at)
Index("ix_job_runs_lease_retry", JobRun.lease_expires_at, JobRun.next_retry_at)
Index("ix_mcp_tokens_expires_at", McpToken.expires_at)
Index("ix_audit_events_retention", AuditEvent.created_at)
