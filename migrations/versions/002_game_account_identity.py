"""Persist authoritative game account identities."""

import sqlalchemy as sa
from alembic import op


revision = "002_game_account_identity"
down_revision = "001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "game_accounts",
        sa.Column("game_account_id", sa.String(length=128), nullable=True),
    )
    op.create_unique_constraint(
        "uq_game_accounts_game_account_id", "game_accounts", ["game_account_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_game_accounts_game_account_id", "game_accounts", type_="unique"
    )
    op.drop_column("game_accounts", "game_account_id")
