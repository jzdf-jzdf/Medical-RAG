"""Add durable queue state and idempotency for evaluation runs."""

import sqlalchemy as sa
from alembic import op


revision = "20261029_0003"
down_revision = "20261023_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evaluation_runs",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("evaluation_runs", sa.Column("worker_task_id", sa.String(255)))
    op.add_column("evaluation_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column("evaluation_runs", sa.Column("last_enqueued_at", sa.DateTime(timezone=True)))
    op.add_column("evaluation_runs", sa.Column("error", sa.Text()))
    op.create_index("ix_evaluation_runs_heartbeat_at", "evaluation_runs", ["heartbeat_at"])
    op.create_unique_constraint(
        "uq_evaluation_result_run_question",
        "evaluation_results",
        ["run_id", "question_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_evaluation_result_run_question", "evaluation_results", type_="unique"
    )
    op.drop_index("ix_evaluation_runs_heartbeat_at", table_name="evaluation_runs")
    for column in [
        "error",
        "last_enqueued_at",
        "heartbeat_at",
        "worker_task_id",
        "cancel_requested",
        "attempt_count",
    ]:
        op.drop_column("evaluation_runs", column)
