"""Allow multiple immutable chunk snapshots for blue/green index switching."""

from alembic import op

revision = "20261020_0006"
down_revision = "20261015_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_chunks_collection_document_index", "chunks", type_="unique")
    op.create_unique_constraint(
        "uq_chunks_collection_document_version_index",
        "chunks",
        ["collection_id", "document_id", "index_version", "chunk_index"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_chunks_collection_document_version_index", "chunks", type_="unique"
    )
    # Downgrading after multiple versions exist requires operators to remove
    # superseded snapshots first; keeping this explicit prevents silent loss.
    op.create_unique_constraint(
        "uq_chunks_collection_document_index",
        "chunks",
        ["collection_id", "document_id", "chunk_index"],
    )
