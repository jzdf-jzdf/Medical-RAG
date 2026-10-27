"""Structured evidence-extraction and human-review domain models."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Disease(Base):
    __tablename__ = "diseases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    ontology_code: Mapped[str | None] = mapped_column(String(128), index=True)


class Study(Base):
    __tablename__ = "studies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    disease_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("diseases.id", ondelete="SET NULL"), index=True
    )
    study_type: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(128))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class PicoElement(Base):
    __tablename__ = "pico_elements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    study_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("studies.id", ondelete="CASCADE"), index=True
    )
    element_type: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    normalized_value: Mapped[dict] = mapped_column(JSONB, default=dict)


class Outcome(Base):
    __tablename__ = "outcomes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    study_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("studies.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(500))
    definition: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)


class EvidenceSpan(Base):
    __tablename__ = "evidence_spans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), index=True
    )
    study_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("studies.id", ondelete="SET NULL")
    )
    quote: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    review_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    schema_version: Mapped[str] = mapped_column(String(128))
    model_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), index=True)
    raw_output: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evidence_span_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_spans.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[str] = mapped_column(String(255))
    decision: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
