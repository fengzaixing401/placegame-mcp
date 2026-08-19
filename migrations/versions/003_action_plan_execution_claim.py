"""Add durable ownership for crash-safe idle execution."""

import sqlalchemy as sa
from alembic import op


revision = "003_action_plan_execution_claim"
down_revision = "002_game_account_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("action_plans", sa.Column("execution_owner", sa.String(length=128), nullable=True))
    op.add_column("action_plans", sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("action_plans", sa.Column("execution_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "action_plans",
        sa.Column("execution_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("action_plans", "execution_attempt_count", server_default=None)


def downgrade() -> None:
    op.drop_column("action_plans", "execution_attempt_count")
    op.drop_column("action_plans", "execution_lease_expires_at")
    op.drop_column("action_plans", "execution_started_at")
    op.drop_column("action_plans", "execution_owner")
