"""Contracts for traceable evidence candidates and human review."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EvidenceSpanRead(BaseModel):
    id: uuid.UUID
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    quote: str
    heading_path: list[str] = Field(default_factory=list)
    page: int | None
    char_start: int
    char_end: int
    review_status: str


class EvidenceReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str | None = Field(default=None, max_length=4000)


class EvidenceReviewRead(BaseModel):
    evidence_span_id: uuid.UUID
    reviewer_id: str
    decision: str
    comment: str | None
    created_at: datetime
