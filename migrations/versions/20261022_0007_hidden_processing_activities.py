"""Persist presentation-only hiding of synthetic processing history."""

import sqlalchemy as sa
from alembic import op

revision = "20261022_0007"
down_revision = "20261020_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hidden_processing_activities",
        sa.Column("activity_id", sa.String(length=255), nullable=False),
        sa.Column(
            "hidden_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("activity_id"),
    )


def downgrade() -> None:
    op.drop_table("hidden_processing_activities")
