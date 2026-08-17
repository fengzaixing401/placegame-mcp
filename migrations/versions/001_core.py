"""Create encrypted core persistence tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("game_username", postgresql.BYTEA(), nullable=True),
        sa.Column("auth_mode", sa.String(length=32), nullable=False),
        sa.Column("password", postgresql.BYTEA(), nullable=True),
        sa.Column("session_token", postgresql.BYTEA(), nullable=True),
        sa.Column("session_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auth_failure_count", sa.Integer(), nullable=False),
        sa.Column("auth_failure_window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("policy_version >= 1", name="ck_game_accounts_policy_version"),
    )
    op.create_table(
        "account_policies",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy", postgresql.JSONB(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["game_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
        sa.CheckConstraint("jsonb_typeof(policy) = 'object'", name="ck_account_policies_policy_object"),
    )
    op.create_index("ix_account_policies_policy_version", "account_policies", ["policy_version"])
    op.create_table(
        "account_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sanitized_state", postgresql.JSONB(), nullable=False),
        sa.Column("state_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["game_accounts.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "expires_at = fetched_at + INTERVAL '5 minutes'",
            name="ck_account_snapshots_snapshot_expiry",
        ),
    )
    op.create_index("ix_account_snapshots_expires_at", "account_snapshots", ["expires_at"])
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("schedule", sa.String(length=128), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("misfire_policy", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["game_accounts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_jobs_due", "jobs", ["enabled", "next_run_at"])
    op.create_table(
        "job_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["game_accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("account_id", "idempotency_key", name="uq_job_runs_account_idempotency"),
    )
    op.create_index("ix_job_runs_lease_retry", "job_runs", ["lease_expires_at", "next_retry_at"])
    op.create_table(
        "action_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("proposed_actions", postgresql.JSONB(), nullable=False),
        sa.Column("estimated_costs", postgresql.JSONB(), nullable=False),
        sa.Column("risk", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmation_required", sa.Boolean(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(length=128), nullable=True),
        sa.Column("execution_state", sa.String(length=32), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_result", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["game_accounts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_action_plans_expires_at", "action_plans", ["expires_at"])
    op.create_table(
        "mcp_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("account_allowlist", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("digest"),
    )
    op.create_index("ix_mcp_tokens_expires_at", "mcp_tokens", ["expires_at"])
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("costs", postgresql.JSONB(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["game_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id"], ["action_plans.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_audit_events_retention", "audit_events", ["created_at"])
    op.create_table(
        "scheduler_leases",
        sa.Column("name", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("name = 'default'", name="ck_scheduler_leases_default_name"),
    )
    op.execute(
        "INSERT INTO scheduler_leases (name, updated_at) VALUES ('default', CURRENT_TIMESTAMP)"
    )
    op.execute(
        """
        CREATE FUNCTION prevent_game_account_policy_version_decrease() RETURNS trigger AS $$
        BEGIN
            IF NEW.policy_version < OLD.policy_version THEN
                RAISE EXCEPTION 'policy_version cannot decrease';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER game_accounts_policy_version_monotonic
        BEFORE UPDATE OF policy_version ON game_accounts
        FOR EACH ROW EXECUTE FUNCTION prevent_game_account_policy_version_decrease()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_audit_event_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_immutable
        BEFORE UPDATE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_audit_event_retention() RETURNS trigger AS $$
        BEGIN
            IF OLD.created_at > CURRENT_TIMESTAMP - INTERVAL '90 days' THEN
                RAISE EXCEPTION 'audit_events are retained for 90 days';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_retention
        BEFORE DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION enforce_audit_event_retention()
        """
    )


def downgrade() -> None:
    op.drop_table("scheduler_leases")
    op.execute("DROP TRIGGER audit_events_immutable ON audit_events")
    op.execute("DROP TRIGGER audit_events_retention ON audit_events")
    op.execute("DROP FUNCTION prevent_audit_event_update")
    op.execute("DROP FUNCTION enforce_audit_event_retention")
    op.drop_index("ix_audit_events_retention", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_mcp_tokens_expires_at", table_name="mcp_tokens")
    op.drop_table("mcp_tokens")
    op.drop_index("ix_action_plans_expires_at", table_name="action_plans")
    op.drop_table("action_plans")
    op.drop_index("ix_job_runs_lease_retry", table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_index("ix_jobs_due", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_account_snapshots_expires_at", table_name="account_snapshots")
    op.drop_table("account_snapshots")
    op.drop_index("ix_account_policies_policy_version", table_name="account_policies")
    op.drop_table("account_policies")
    op.execute("DROP TRIGGER game_accounts_policy_version_monotonic ON game_accounts")
    op.execute("DROP FUNCTION prevent_game_account_policy_version_decrease")
    op.drop_table("game_accounts")
