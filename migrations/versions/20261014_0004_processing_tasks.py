"""Add a durable PostgreSQL-backed processing task queue."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261014_0004"
down_revision = "20261013_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the durable queue, lookup indexes, and active-job invariant."""

    # The table deliberately has no foreign key to documents: task history may
    # outlive a resource in future cleanup flows, while resource_id still keeps
    # the audit relationship visible.
    op.create_table(
        "processing_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_name", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("phase", sa.String(64), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "result",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("claimed_by", sa.String(255), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_processing_tasks_status",
        ),
        sa.CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 100)",
            name="ck_processing_tasks_progress",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_processing_tasks_task_type", "processing_tasks", ["task_type"])
    # Worker polling reads queued rows by status and creation order.
    op.create_index(
        "ix_processing_tasks_status_created",
        "processing_tasks",
        ["status", "created_at"],
    )
    # Resource lookups protect attachments while a task is active.
    op.create_index(
        "ix_processing_tasks_resource",
        "processing_tasks",
        ["resource_type", "resource_id"],
    )
    # Terminal history is excluded, allowing later conversions while preventing
    # two queued/running tasks of the same type for one resource.
    op.create_index(
        "uq_processing_tasks_active_resource",
        "processing_tasks",
        ["task_type", "resource_type", "resource_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    """Remove indexes before dropping the queue table."""

    op.drop_index("uq_processing_tasks_active_resource", table_name="processing_tasks")
    op.drop_index("ix_processing_tasks_resource", table_name="processing_tasks")
    op.drop_index("ix_processing_tasks_status_created", table_name="processing_tasks")
    op.drop_index("ix_processing_tasks_task_type", table_name="processing_tasks")
    op.drop_table("processing_tasks")
