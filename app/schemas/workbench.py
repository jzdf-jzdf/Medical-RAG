"""Contracts for the chunk-to-question Agent testing workbench."""

from typing import Any

from pydantic import BaseModel, Field, field_validator


class DocumentSummary(BaseModel):
    id: str
    title: str


class KnowledgeBaseSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: str
    documents: list[DocumentSummary] = Field(default_factory=list)


class ChunkItem(BaseModel):
    index: int
    content: str
    embedding_text: str | None = None
    char_start: int = 0
    char_end: int = 0
    char_count: int = 0
    heading_path: list[str] = Field(default_factory=list)


class ChunkStats(BaseModel):
    total: int
    average_chars: int
    minimum_chars: int
    maximum_chars: int
    too_short: int = 0
    too_long: int = 0


class DocumentChunks(BaseModel):
    document_id: str
    document_title: str
    method_name: str
    method_strategy: str
    chunks: list[ChunkItem]
    stats: ChunkStats


class GenerateQuestionRequest(BaseModel):
    collection_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    chunk_indexes: list[int] = Field(min_length=1, max_length=8)
    instructions: str | None = Field(default=None, max_length=1000)
    model_config_id: str | None = None

    @field_validator("chunk_indexes")
    @classmethod
    def unique_chunk_indexes(cls, value: list[int]) -> list[int]:
        if any(item < 0 for item in value):
            raise ValueError("分块序号不能小于 0")
        if len(set(value)) != len(value):
            raise ValueError("分块序号不能重复")
        return value


class EvidenceExcerpt(BaseModel):
    chunk_index: int
    heading: str = ""
    quote: str = Field(min_length=1)


class GeneratedQuestion(BaseModel):
    question: str = Field(min_length=1)
    standard_answer: str = Field(min_length=1)
    difficulty: str = "中等"
    topic: str = ""
    rationale: str = ""
    evidence: list[EvidenceExcerpt] = Field(default_factory=list)
    source_chunks: list[ChunkItem] = Field(default_factory=list)
    agent_run_id: str
    model: str
    provider: str


class AgentTestRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    expected_answer: str | None = Field(default=None, max_length=16000)
    model_config_id: str | None = None


class AgentTestResult(BaseModel):
    answer: str
    expected_answer: str | None = None
    run_id: str
    model: str
    provider: str
    degraded_without_evidence: bool
    citations: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)
