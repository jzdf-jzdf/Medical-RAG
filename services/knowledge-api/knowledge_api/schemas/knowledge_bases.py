"""API contracts for build configuration, previews, and knowledge bases."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from knowledge_api.schemas.documents import DocumentRead


class ChunkingMethodCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    strategy: str = Field(min_length=1, max_length=64)
    description: str | None = None
    color: str = Field(default="#5f918c", pattern=r"^#[0-9a-fA-F]{6}$")
    config: dict = Field(default_factory=dict)


class ChunkingMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    strategy: str
    description: str | None
    color: str
    config: dict
    created_at: datetime


class EmbeddingModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=512)
    dimension: int | None = Field(default=None, ge=1, le=1_000_000)
    base_url: str | None = Field(default=None, max_length=2048)
    config: dict = Field(default_factory=dict)


class EmbeddingModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    provider: str
    model_name: str
    dimension: int | None
    base_url: str | None
    config: dict
    created_at: datetime


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    document_ids: list[uuid.UUID] = Field(min_length=1)
    chunking_method_id: uuid.UUID
    embedding_model_id: uuid.UUID


class KnowledgeBaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    status: str
    chunking_method: ChunkingMethodRead | None
    embedding_model: EmbeddingModelRead | None
    documents: list[DocumentRead]
    created_at: datetime
    updated_at: datetime


class ChunkPreviewItem(BaseModel):
    index: int
    content: str
    embedding_text: str
    char_start: int
    char_end: int
    char_count: int
    heading_path: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class ChunkPreviewStats(BaseModel):
    total: int
    average_chars: int
    minimum_chars: int
    maximum_chars: int
    too_short: int
    too_long: int


class ChunkPreviewRequest(BaseModel):
    document_id: uuid.UUID
    strategy: str
    config: dict = Field(default_factory=dict)


class ChunkRecipePreview(BaseModel):
    document_id: uuid.UUID
    document_title: str
    strategy: str
    config: dict
    chunks: list[ChunkPreviewItem]
    stats: ChunkPreviewStats


class DocumentChunkPreview(BaseModel):
    document_id: uuid.UUID
    document_title: str
    method: ChunkingMethodRead
    chunks: list[ChunkPreviewItem]
    stats: ChunkPreviewStats


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=50)
    minimum_score: float = Field(default=0.0, ge=0.0, le=1.0)
    strategy: str = Field(default="hybrid", pattern="^(hybrid|dense|bm25)$")
    use_reranker: bool = True
    index_version: str | None = Field(default=None, max_length=128)


class KnowledgeSearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    content: str
    heading_path: list[str]
    metadata: dict = Field(default_factory=dict)
    page_start: int | None
    page_end: int | None
    score: float
    vector_score: float | None = None
    lexical_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    rank: int
    index_version: str
    citation: dict


class KnowledgeSearchResponse(BaseModel):
    collection_id: uuid.UUID
    collection_name: str
    query: str
    count: int
    items: list[KnowledgeSearchHit]
    strategy: str
    index_version: str
    trace_id: str
    trace: list[dict]


class IndexVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version: str
    status: str
    embedding_model: str
    reranker_model: str | None = None
    chunk_count: int
    config: dict
    created_at: datetime
    activated_at: datetime | None = None


class IndexVersionList(BaseModel):
    active_version: str | None = None
    items: list[IndexVersionRead]
