"""Parsed document sections and persisted knowledge-base chunks."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.collections import Collection
    from app.models.documents import DocumentVersion


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE")
    )
    heading: Mapped[str] = mapped_column(String(1000))
    level: Mapped[int] = mapped_column(Integer)
    order_index: Mapped[int] = mapped_column(Integer)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    document_version: Mapped["DocumentVersion"] = relationship(back_populates="sections")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )


class Chunk(Base):
    """A stable chunk snapshot produced for one document in one collection."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "collection_id",
            "document_id",
            "index_version",
            "chunk_index",
            name="uq_chunks_collection_document_version_index",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), index=True
    )
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    document_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_files.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int | None] = mapped_column(Integer)
    index_version: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text)
    embedding_text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSONB)
    heading_path: Mapped[list] = mapped_column(JSONB, default=list)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    section: Mapped[Section | None] = relationship(back_populates="chunks")
    collection: Mapped["Collection | None"] = relationship(back_populates="chunks")
