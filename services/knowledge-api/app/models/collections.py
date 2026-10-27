"""Knowledge-base model and its references to documents and build recipes."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.associations import collection_documents

if TYPE_CHECKING:
    from app.models.chunks import Chunk
    from app.models.configurations import ChunkingMethod, EmbeddingModel
    from app.models.documents import Document


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    chunking_method_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunking_methods.id", ondelete="RESTRICT"), index=True
    )
    embedding_model_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("embedding_models.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="configured", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    documents: Mapped[list["Document"]] = relationship(
        secondary=collection_documents, back_populates="collections"
    )
    chunking_method: Mapped["ChunkingMethod | None"] = relationship(back_populates="collections")
    embedding_model: Mapped["EmbeddingModel | None"] = relationship(back_populates="collections")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan"
    )
