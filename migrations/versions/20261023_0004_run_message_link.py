"""Link completed agent runs to their persisted assistant messages."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261023_0004"
down_revision = "20261020_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("assistant_message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runs_assistant_message_id",
        "agent_runs",
        "messages",
        ["assistant_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_agent_runs_assistant_message_id",
        "agent_runs",
        ["assistant_message_id"],
    )
    op.execute(
        """
        WITH ranked_runs AS (
            SELECT id, conversation_id,
                   row_number() OVER (
                       PARTITION BY conversation_id ORDER BY started_at, id
                   ) AS turn_number
            FROM agent_runs
            WHERE status = 'completed'
        ),
        ranked_answers AS (
            SELECT id, conversation_id,
                   row_number() OVER (
                       PARTITION BY conversation_id ORDER BY created_at, id
                   ) AS turn_number
            FROM messages
            WHERE role = 'assistant'
        )
        UPDATE agent_runs AS run
        SET assistant_message_id = answer.id
        FROM ranked_runs AS ranked_run
        JOIN ranked_answers AS answer
          ON answer.conversation_id = ranked_run.conversation_id
         AND answer.turn_number = ranked_run.turn_number
        WHERE run.id = ranked_run.id
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_agent_runs_assistant_message_id", "agent_runs", type_="unique"
    )
    op.drop_constraint(
        "fk_agent_runs_assistant_message_id", "agent_runs", type_="foreignkey"
    )
    op.drop_column("agent_runs", "assistant_message_id")
