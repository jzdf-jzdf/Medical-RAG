"""Initial agent schema."""

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
        "conversations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_table(
        "messages",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "conversation_id",
            uuid,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_table(
        "agent_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "conversation_id",
            uuid,
            sa.ForeignKey("conversations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_message_id",
            uuid,
            sa.ForeignKey("messages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model_provider", sa.String(128), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("prompt_version", sa.String(128), nullable=False),
        sa.Column("skill_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "degraded_without_evidence", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_table(
        "tool_calls",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "agent_run_id", uuid, sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("mcp_server", sa.String(255), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("result_hash", sa.String(64)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tool_calls_agent_run_id", "tool_calls", ["agent_run_id"])
    op.create_table(
        "answer_citations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "message_id", uuid, sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "tool_call_id",
            uuid,
            sa.ForeignKey("tool_calls.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("document_id", sa.String(255)),
        sa.Column("document_version_id", sa.String(255)),
        sa.Column("chunk_id", sa.String(255)),
        sa.Column("evidence_span_id", sa.String(255)),
        sa.Column("index_version", sa.String(255)),
        sa.Column("citation_order", sa.Integer(), nullable=False),
        sa.Column("source_server", sa.String(255), nullable=False),
        sa.Column("external_source_id", sa.String(512)),
        sa.UniqueConstraint("message_id", "citation_order", name="uq_message_citation_order"),
    )
    op.create_index("ix_answer_citations_message_id", "answer_citations", ["message_id"])
    op.create_table(
        "agent_skills",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_skill_version"),
    )


def downgrade() -> None:
    for table in [
        "agent_skills",
        "answer_citations",
        "tool_calls",
        "agent_runs",
        "messages",
        "conversations",
    ]:
        op.drop_table(table)
