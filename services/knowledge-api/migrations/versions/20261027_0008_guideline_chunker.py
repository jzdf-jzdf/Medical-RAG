"""Register the bundled medical guideline semantic engine."""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261027_0008"
down_revision = "20261022_0007"
branch_labels = None
depends_on = None
METHOD_ID = uuid.UUID("c1000000-0000-4000-8000-000000000004")


def upgrade() -> None:
    methods = sa.table(
        "chunking_methods",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String()),
        sa.column("strategy", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("color", sa.String()),
        sa.column("config", postgresql.JSONB()),
    )
    op.get_bind().execute(
        postgresql.insert(methods)
        .values(
            id=METHOD_ID,
            name="医学指南原子知识分块（Guideline Chunker）",
            strategy="guideline_semantic",
            color="#527aa3",
            description="规则原子化 + DeepSeek 语义判断；FACT/REC、受控主题、推荐等级、连续原文、缓存与质量门。",
            config={
                "deepseek": {
                    "model": "deepseek-v4-flash",
                    "timeout_seconds": 180,
                    "max_attempts": 3,
                    "concurrent_requests": 1,
                    "json_output": True,
                    "api_key_env": "DEEPSEEK_API_KEY",
                },
                "batching": {
                    "target_chars": 2600,
                    "max_chars": 4200,
                    "target_atoms": 12,
                    "max_atoms": 18,
                    "context_before_atoms": 2,
                    "context_after_atoms": 2,
                },
                "chunking": {"review_max_chars": 500},
                "summary": {"enabled": True, "max_chars": 300},
                "cache_enabled": True,
                "require_quality_gate": True,
                "prepend_heading_path": True,
                "max_chunks": 10000,
            },
        )
        .on_conflict_do_nothing()
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM chunking_methods WHERE id = :id AND NOT EXISTS (SELECT 1 FROM collections WHERE chunking_method_id = :id)"
        ).bindparams(id=METHOD_ID)
    )
