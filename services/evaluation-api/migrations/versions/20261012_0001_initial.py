"""Initial evaluation schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261012_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "datasets",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("name", sa.String(500), nullable=False, unique=True),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "questions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("gold_answer", sa.Text(), nullable=False),
        sa.Column("gold_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_questions_dataset_id", "questions", ["dataset_id"])
    op.create_table(
        "evaluation_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "dataset_id", uuid, sa.ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("total_questions", sa.Integer(), nullable=False),
        sa.Column("completed_questions", sa.Integer(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_evaluation_runs_dataset_id", "evaluation_runs", ["dataset_id"])
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])
    op.create_table(
        "evaluation_results",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "run_id", uuid, sa.ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "question_id", uuid, sa.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("agent_run_id", sa.String(255)),
        sa.Column("answer", sa.Text()),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("overall_score", sa.Float()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_evaluation_results_run_id", "evaluation_results", ["run_id"])


def downgrade() -> None:
    for table in ["evaluation_results", "evaluation_runs", "questions", "datasets"]:
        op.drop_table(table)
