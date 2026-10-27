"""Initial knowledge schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261012_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("doi", sa.String(255), nullable=True),
        sa.Column("authors", postgresql.JSONB(), nullable=False),
        sa.Column("abstract", sa.Text()),
        sa.Column("publication_year", sa.Integer()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("doi"),
    )
    op.create_index("ix_documents_title", "documents", ["title"])
    op.create_index("ix_documents_doi", "documents", ["doi"])
    op.create_table(
        "collections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "document_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_name", sa.String(512), nullable=False),
        sa.Column("storage_path", sa.String(2048), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("sha256", name="uq_document_files_sha256"),
    )
    op.create_index("ix_document_files_document_id", "document_files", ["document_id"])
    op.create_index("ix_document_files_sha256", "document_files", ["sha256"])
    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parser", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("markdown_path", sa.String(2048)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("document_id", "version", name="uq_document_version"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_status", "document_versions", ["status"])
    op.create_table(
        "collection_documents",
        sa.Column(
            "collection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sections.id", ondelete="CASCADE"),
        ),
        sa.Column("heading", sa.String(1000), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
    )
    op.create_index("ix_sections_document_version_id", "sections", ["document_version_id"])
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "section_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index_version", sa.String(128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_chunks_section_id", "chunks", ["section_id"])
    op.create_index("ix_chunks_index_version", "chunks", ["index_version"])
    op.create_table(
        "diseases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("aliases", postgresql.JSONB(), nullable=False),
        sa.Column("ontology_code", sa.String(128)),
    )
    op.create_index("ix_diseases_ontology_code", "diseases", ["ontology_code"])
    op.create_table(
        "studies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "disease_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("diseases.id", ondelete="SET NULL"),
        ),
        sa.Column("study_type", sa.String(128)),
        sa.Column("schema_version", sa.String(128), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_studies_document_version_id", "studies", ["document_version_id"])
    op.create_index("ix_studies_disease_id", "studies", ["disease_id"])
    op.create_table(
        "pico_elements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("element_type", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_value", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_pico_elements_study_id", "pico_elements", ["study_id"])
    op.create_table(
        "outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("definition", sa.Text()),
        sa.Column("result", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_outcomes_study_id", "outcomes", ["study_id"])
    op.create_table(
        "evidence_spans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="SET NULL"),
        ),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("review_status", sa.String(32), nullable=False),
    )
    op.create_index("ix_evidence_spans_chunk_id", "evidence_spans", ["chunk_id"])
    op.create_index("ix_evidence_spans_review_status", "evidence_spans", ["review_status"])
    op.create_table(
        "extraction_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(128), nullable=False),
        sa.Column("model_name", sa.String(255)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("raw_output", postgresql.JSONB()),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_extraction_runs_document_version_id", "extraction_runs", ["document_version_id"]
    )
    op.create_index("ix_extraction_runs_status", "extraction_runs", ["status"])
    op.create_table(
        "review_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "evidence_span_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_spans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewer_id", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_review_decisions_evidence_span_id", "review_decisions", ["evidence_span_id"]
    )
    op.create_table(
        "index_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(128), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("embedding_model", sa.String(255), nullable=False),
        sa.Column("reranker_model", sa.String(255)),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_index_versions_status", "index_versions", ["status"])


def downgrade() -> None:
    for table in [
        "index_versions",
        "review_decisions",
        "extraction_runs",
        "evidence_spans",
        "outcomes",
        "pico_elements",
        "studies",
        "diseases",
        "chunks",
        "sections",
        "collection_documents",
        "document_versions",
        "document_files",
        "collections",
        "documents",
    ]:
        op.drop_table(table)
