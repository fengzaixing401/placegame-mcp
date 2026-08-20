"""Add the single-operator WebUI credential and sessions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "004_admin_sessions"
down_revision = "003_action_plan_execution_claim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_admin_credentials_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest", name="uq_admin_sessions_token_digest"),
    )
    op.create_index(
        "ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_admin_sessions_expires_at", table_name="admin_sessions")
    op.drop_table("admin_sessions")
    op.drop_table("admin_credentials")
