"""Durable background jobs executed by the local PostgreSQL-backed worker."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from knowledge_api.db.base import Base


class ProcessingTask(Base):
    """A restart-safe job record; PostgreSQL is both queue and source of truth.

    ``status`` describes the durable lifecycle (queued/running/terminal), while
    ``phase`` describes the finer-grained step shown to the user. Keeping both
    in one row lets a browser reconnect after a restart without relying on
    process memory or an in-flight HTTP response.
    """

    __tablename__ = "processing_tasks"
    __table_args__ = (
        # The Worker normally searches by status and FIFO creation time. The
        # resource index supports document-level mutation guards and history.
        Index("ix_processing_tasks_status_created", "status", "created_at"),
        Index("ix_processing_tasks_resource", "resource_type", "resource_id"),
        # Only active rows participate in this unique index. Completed and
        # failed history is retained, but one document cannot have two live
        # conversions even when two clients enqueue concurrently.
        Index(
            "uq_processing_tasks_active_resource",
            "task_type",
            "resource_type",
            "resource_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    # Task identity and routing: task_type chooses the executor, while the
    # resource fields identify the domain object affected by the job.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    resource_name: Mapped[str] = mapped_column(String(1000))
    # Durable lifecycle and user-facing execution state. A nullable progress
    # means the current tool phase is indeterminate, not zero percent.
    status: Mapped[str] = mapped_column(String(32), default="queued")
    phase: Mapped[str] = mapped_column(String(64), default="queued")
    progress: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)
    # Payload captures immutable enqueue options; result stores stable output
    # identifiers so callers can inspect a completed job without rerunning it.
    payload_json: Mapped[dict] = mapped_column("payload", JSONB, default=dict)
    result_json: Mapped[dict] = mapped_column("result", JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    # Retry/lease fields make a running row recoverable after process failure.
    # claimed_by is diagnostic; heartbeat_at determines whether the lease is stale.
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    claimed_by: Mapped[str | None] = mapped_column(String(255))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Timestamps distinguish queue wait, first execution, terminal completion,
    # and the latest status update for API consumers and operational debugging.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def name(self) -> str:
        """Expose the domain name expected by the normalized task API."""

        return self.resource_name

    @property
    def has_chunking_result(self) -> bool:
        return bool((self.result_json or {}).get("run_id"))

    @property
    def operation(self) -> str:
        """Translate an internal executor key into a user-facing operation."""

        return {
            "pdf_to_markdown": "PDF 转 Markdown",
            "knowledge_base_build": "知识库向量构建",
            "guideline_chunking": "医学指南语义切块",
            "embedding_model_download": "MedCPT 模型下载",
            "evidence_extraction": "证据候选抽取",
        }.get(self.task_type, "后台处理")

    @property
    def processor(self) -> str:
        """Describe the concrete processor used for this task type."""

        return {
            "pdf_to_markdown": "MinerU 4",
            "knowledge_base_build": "MedCPT 分块与嵌入 Worker",
            "guideline_chunking": "Guideline Chunker / DeepSeek",
            "embedding_model_download": "Hugging Face / MedCPT",
            "evidence_extraction": "证据抽取 Worker",
        }.get(self.task_type, "后台 Worker")


class HiddenProcessingActivity(Base):
    """A presentation-only tombstone for synthetic task-history entries."""

    __tablename__ = "hidden_processing_activities"

    activity_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    hidden_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
