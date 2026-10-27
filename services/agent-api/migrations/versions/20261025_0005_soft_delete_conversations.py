"""Soft-delete conversations to preserve messages and agent run logs."""

import sqlalchemy as sa
from alembic import op

revision = "20261025_0005"
down_revision = "20261023_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "deleted_at")
