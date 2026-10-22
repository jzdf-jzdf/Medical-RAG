"""Add persisted embeddings and collection-scoped index metadata."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261015_0005"
down_revision = "20261014_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("embedding", postgresql.JSONB(), nullable=True))
    op.add_column(
        "index_versions",
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "index_versions",
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("index_versions", sa.Column("error_message", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_index_versions_collection",
        "index_versions",
        "collections",
        ["collection_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_index_versions_collection_id", "index_versions", ["collection_id"]
    )
    # Upgrade the original placeholder local registration to the two official
    # MedCPT encoders used by the real index and query paths.
    op.execute(
        """
        UPDATE embedding_models
        SET name = 'MedCPT（本地双编码器）', provider = 'medcpt',
            model_name = 'ncbi/MedCPT-Article-Encoder', dimension = 768,
            config = COALESCE(config, '{}'::jsonb) ||
              '{"normalize": true, "query_model": "ncbi/MedCPT-Query-Encoder", "article_model": "ncbi/MedCPT-Article-Encoder"}'::jsonb
        WHERE provider = 'local' AND base_url IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_index_versions_collection_id", table_name="index_versions")
    op.drop_constraint(
        "fk_index_versions_collection", "index_versions", type_="foreignkey"
    )
    op.drop_column("index_versions", "error_message")
    op.drop_column("index_versions", "chunk_count")
    op.drop_column("index_versions", "collection_id")
    op.drop_column("chunks", "embedding")
