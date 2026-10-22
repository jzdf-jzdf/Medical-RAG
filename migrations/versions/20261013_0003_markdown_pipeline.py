"""Enforce one PDF and one Markdown file and persist knowledge chunks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261013_0003"
down_revision = "20261013_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_files", sa.Column("kind", sa.String(16), nullable=True))
    op.add_column(
        "document_files",
        sa.Column("source", sa.String(32), nullable=False, server_default="upload"),
    )
    op.execute(
        """
        UPDATE document_files
        SET kind = CASE
            WHEN lower(original_name) LIKE '%.md' THEN 'markdown'
            ELSE 'pdf'
        END
        """
    )
    op.alter_column("document_files", "kind", nullable=False)
    op.create_index("ix_document_files_kind", "document_files", ["kind"])
    op.create_unique_constraint(
        "uq_document_files_document_kind", "document_files", ["document_id", "kind"]
    )

    op.alter_column("chunks", "section_id", existing_type=postgresql.UUID(), nullable=True)
    op.add_column(
        "chunks", sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("chunks", sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "chunks", sa.Column("document_file_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("chunks", sa.Column("chunk_index", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("embedding_text", sa.Text(), nullable=True))
    op.add_column(
        "chunks",
        sa.Column(
            "heading_path",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "chunks",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_foreign_key(
        "fk_chunks_collection",
        "chunks",
        "collections",
        ["collection_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_chunks_document", "chunks", "documents", ["document_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_chunks_document_file",
        "chunks",
        "document_files",
        ["document_file_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_chunks_collection_id", "chunks", ["collection_id"])
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_document_file_id", "chunks", ["document_file_id"])
    op.create_unique_constraint(
        "uq_chunks_collection_document_index",
        "chunks",
        ["collection_id", "document_id", "chunk_index"],
    )


def downgrade() -> None:
    # Collection chunks introduced by this revision do not belong to a legacy
    # Section. Remove them before restoring the old non-null section constraint.
    op.execute("DELETE FROM chunks WHERE section_id IS NULL")
    op.drop_constraint("uq_chunks_collection_document_index", "chunks", type_="unique")
    op.drop_index("ix_chunks_document_file_id", table_name="chunks")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_index("ix_chunks_collection_id", table_name="chunks")
    op.drop_constraint("fk_chunks_document_file", "chunks", type_="foreignkey")
    op.drop_constraint("fk_chunks_document", "chunks", type_="foreignkey")
    op.drop_constraint("fk_chunks_collection", "chunks", type_="foreignkey")
    op.drop_column("chunks", "created_at")
    op.drop_column("chunks", "heading_path")
    op.drop_column("chunks", "embedding_text")
    op.drop_column("chunks", "chunk_index")
    op.drop_column("chunks", "document_file_id")
    op.drop_column("chunks", "document_id")
    op.drop_column("chunks", "collection_id")
    op.alter_column("chunks", "section_id", existing_type=postgresql.UUID(), nullable=False)

    op.drop_constraint("uq_document_files_document_kind", "document_files", type_="unique")
    op.drop_index("ix_document_files_kind", table_name="document_files")
    op.drop_column("document_files", "source")
    op.drop_column("document_files", "kind")
