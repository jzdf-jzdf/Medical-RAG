"""Literature-entry, attachment, and parsed-version persistence models."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.associations import collection_documents

if TYPE_CHECKING:
    from app.models.chunks import Section
    from app.models.collections import Collection


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("doi"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(1000), index=True)
    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    authors: Mapped[list] = mapped_column(JSONB, default=list)
    abstract: Mapped[str | None] = mapped_column(Text)
    publication_year: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    files: Mapped[list["DocumentFile"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    collections: Mapped[list["Collection"]] = relationship(
        secondary=collection_documents, back_populates="documents"
    )


class DocumentFile(Base):
    """A physical attachment; one document may own one PDF and one Markdown file."""

    __tablename__ = "document_files"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_document_files_sha256"),
        UniqueConstraint("document_id", "kind", name="uq_document_files_document_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(32), default="upload")
    storage_path: Mapped[str] = mapped_column(String(2048))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    media_type: Mapped[str] = mapped_column(String(128), default="application/pdf")
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    document: Mapped[Document] = relationship(back_populates="files")


class DocumentVersion(Base):
    """Tracks each Markdown generation so parsing provenance remains auditable."""

    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_document_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    parser: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    markdown_path: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    document: Mapped[Document] = relationship(back_populates="versions")
    sections: Mapped[list["Section"]] = relationship(
        back_populates="document_version", cascade="all, delete-orphan"
    )
