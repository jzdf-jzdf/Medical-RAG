import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500))
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), index=True
    )
    user_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT")
    )
    model_provider: Mapped[str] = mapped_column(String(128))
    model_name: Mapped[str] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(128))
    skill_version: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    degraded_without_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    mcp_server: Mapped[str] = mapped_column(String(255))
    tool_name: Mapped[str] = mapped_column(String(255))
    arguments: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict | list | None] = mapped_column(JSONB)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnswerCitation(Base):
    __tablename__ = "answer_citations"
    __table_args__ = (
        UniqueConstraint("message_id", "citation_order", name="uq_message_citation_order"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    tool_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="RESTRICT")
    )
    document_id: Mapped[str | None] = mapped_column(String(255))
    document_version_id: Mapped[str | None] = mapped_column(String(255))
    chunk_id: Mapped[str | None] = mapped_column(String(255))
    evidence_span_id: Mapped[str | None] = mapped_column(String(255))
    index_version: Mapped[str | None] = mapped_column(String(255))
    citation_order: Mapped[int] = mapped_column(Integer)
    source_server: Mapped[str] = mapped_column(String(255))
    external_source_id: Mapped[str | None] = mapped_column(String(512))


class AgentSkill(Base):
    __tablename__ = "agent_skills"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_skill_version"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(128))
    system_prompt: Mapped[str] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
