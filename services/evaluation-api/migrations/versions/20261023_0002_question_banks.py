"""Extend evaluation datasets into first-class question banks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261023_0002"
down_revision = "20261012_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "questions",
        sa.Column("question_type", sa.String(32), server_default="qa", nullable=False),
    )
    op.add_column(
        "questions",
        sa.Column("options", postgresql.JSONB(), server_default="[]", nullable=False),
    )
    op.add_column("questions", sa.Column("correct_answer", sa.String(32)))
    op.add_column(
        "questions",
        sa.Column("source", sa.String(32), server_default="manual", nullable=False),
    )
    op.add_column(
        "questions",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_questions_question_type", "questions", ["question_type"])
    op.create_index("ix_questions_source", "questions", ["source"])


def downgrade() -> None:
    op.drop_index("ix_questions_source", table_name="questions")
    op.drop_index("ix_questions_question_type", table_name="questions")
    for column in ["created_at", "source", "correct_answer", "options", "question_type"]:
        op.drop_column("questions", column)
    op.drop_column("datasets", "updated_at")
