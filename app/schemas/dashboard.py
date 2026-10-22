"""Read models for the real-data dashboard and persisted processing activity."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RecentDocumentRead(BaseModel):
    id: uuid.UUID
    title: str
    item_type: str
    attachment_count: int
    chunk_count: int
    has_pdf: bool
    has_markdown: bool
    created_at: datetime


class DashboardRead(BaseModel):
    documents_total: int
    documents_this_week: int
    markdown_ready: int
    knowledge_bases: int
    persisted_chunks: int
    recent_documents: list[RecentDocumentRead] = Field(default_factory=list)


class ProcessingTaskRead(BaseModel):
    """Normalized activity returned for durable jobs and historical events.

    UUID IDs identify real queue rows and support retry/detail endpoints. String
    IDs identify read-only synthetic history. ``progress=None`` tells the client
    to render an indeterminate indicator rather than a misleading zero.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str | uuid.UUID
    resource_id: uuid.UUID
    resource_type: str
    name: str
    operation: str
    processor: str
    status: str
    phase: str = "finished"
    progress: int | None = Field(default=None, ge=0, le=100)
    message: str | None = None
    error_message: str | None = None
    attempt_count: int = 0
    max_attempts: int = 0
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None


class ProcessingTaskBulkDeleteRequest(BaseModel):
    """IDs of durable, terminal Worker tasks to remove atomically."""

    task_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class ProcessingTaskBulkDeleteRead(BaseModel):
    deleted_ids: list[uuid.UUID]
    deleted_count: int


class ProcessingActivityRemoveRequest(BaseModel):
    """Real task IDs to delete or synthetic history IDs to hide."""

    activity_ids: list[str] = Field(min_length=1, max_length=200)


class ProcessingActivityRemoveRead(BaseModel):
    removed_ids: list[str]
    removed_count: int
