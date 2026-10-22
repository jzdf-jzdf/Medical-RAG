"""Add knowledge-base build configuration registries."""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261013_0002"
down_revision = "20261012_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chunking_methods",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("color", sa.String(16), nullable=False, server_default="#5f918c"),
        sa.Column(
            "config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_chunking_methods_strategy", "chunking_methods", ["strategy"])

    op.create_table(
        "embedding_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(512), nullable=False),
        sa.Column("dimension", sa.Integer()),
        sa.Column("base_url", sa.String(2048)),
        sa.Column(
            "config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_embedding_models_provider", "embedding_models", ["provider"])

    chunking_methods = sa.table(
        "chunking_methods",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String()),
        sa.column("strategy", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("color", sa.String()),
        sa.column("config", postgresql.JSONB()),
    )
    embedding_models = sa.table(
        "embedding_models",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String()),
        sa.column("provider", sa.String()),
        sa.column("model_name", sa.String()),
        sa.column("dimension", sa.Integer()),
        sa.column("base_url", sa.String()),
        sa.column("config", postgresql.JSONB()),
    )
    op.bulk_insert(
        chunking_methods,
        [
            {
                "id": uuid.UUID("c1000000-0000-4000-8000-000000000001"),
                "name": "标题层级分块",
                "strategy": "heading",
                "description": "优先保留 Markdown 标题与段落结构，适合指南和综述。",
                "color": "#5f918c",
                "config": {"max_chars": 1200, "overlap": 120},
            },
            {
                "id": uuid.UUID("c1000000-0000-4000-8000-000000000002"),
                "name": "递归字符分块",
                "strategy": "recursive",
                "description": "按段落和字符长度递归切分，适合结构不稳定的 PDF 文本。",
                "color": "#d58b68",
                "config": {"max_chars": 800, "overlap": 100},
            },
        ],
    )
    op.bulk_insert(
        embedding_models,
        [
            {
                "id": uuid.UUID("e1000000-0000-4000-8000-000000000001"),
                "name": "BGE-M3（本地）",
                "provider": "local",
                "model_name": "BAAI/bge-m3",
                "dimension": 1024,
                "base_url": None,
                "config": {"normalize": True},
            }
        ],
    )

    op.add_column("collections", sa.Column("chunking_method_id", postgresql.UUID(as_uuid=True)))
    op.add_column("collections", sa.Column("embedding_model_id", postgresql.UUID(as_uuid=True)))
    op.add_column(
        "collections",
        sa.Column("status", sa.String(32), nullable=False, server_default="configured"),
    )
    op.add_column(
        "collections",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_foreign_key(
        "fk_collections_chunking_method",
        "collections",
        "chunking_methods",
        ["chunking_method_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_collections_embedding_model",
        "collections",
        "embedding_models",
        ["embedding_model_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_collections_chunking_method_id", "collections", ["chunking_method_id"])
    op.create_index("ix_collections_embedding_model_id", "collections", ["embedding_model_id"])
    op.create_index("ix_collections_status", "collections", ["status"])


def downgrade() -> None:
    op.drop_index("ix_collections_status", table_name="collections")
    op.drop_index("ix_collections_embedding_model_id", table_name="collections")
    op.drop_index("ix_collections_chunking_method_id", table_name="collections")
    op.drop_constraint("fk_collections_embedding_model", "collections", type_="foreignkey")
    op.drop_constraint("fk_collections_chunking_method", "collections", type_="foreignkey")
    op.drop_column("collections", "updated_at")
    op.drop_column("collections", "status")
    op.drop_column("collections", "embedding_model_id")
    op.drop_column("collections", "chunking_method_id")
    op.drop_index("ix_embedding_models_provider", table_name="embedding_models")
    op.drop_table("embedding_models")
    op.drop_index("ix_chunking_methods_strategy", table_name="chunking_methods")
    op.drop_table("chunking_methods")
