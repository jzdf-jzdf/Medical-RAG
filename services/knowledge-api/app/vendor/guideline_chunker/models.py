"""切块流水线共享的严格可序列化契约。"""

from datetime import datetime
from enum import Enum
from pathlib import Path
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """在每个流水线边界拒绝拼写错误或未声明的字段。"""

    model_config = ConfigDict(extra="forbid")


REASON_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{2,63}$"


def _normalize_reason_code(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return re.sub(r"[-\s]+", "_", value.strip().upper())


class AtomKind(str, Enum):
    SENTENCE = "sentence"
    LIST_ITEM = "list_item"
    RECOMMENDATION = "recommendation"
    TABLE = "table"
    CAPTION = "caption"


class ChunkType(str, Enum):
    FACT = "fact"
    RECOMMENDATION = "recommendation"


class AuditDecision(str, Enum):
    ACCEPTED = "accepted"
    EXCLUDED = "excluded"
    NEEDS_REVIEW = "needs_review"
    LLM_FAILED = "llm_failed"
    VALIDATION_FAILED = "validation_failed"
    MANUALLY_RESOLVED = "manually_resolved"


class RunStatus(str, Enum):
    RUNNING = "running"
    DRY_RUN_COMPLETED = "dry_run_completed"
    COMPLETED = "completed"
    COMPLETED_WITH_REVIEW = "completed_with_review"
    FAILED = "failed"


class RecommendationCoverageStatus(str, Enum):
    NOT_CANDIDATE = "not_candidate"
    COVERED = "covered"
    UNCOVERED = "uncovered"
    PENDING_REVIEW = "pending_review"
    FAILED = "failed"
    NOT_IN_SCOPE = "not_in_scope"


class SourceAtom(StrictModel):
    """一个原文单元，内部使用半开区间 ``[start_char, end_char)``。"""

    atom_id: str
    kind: AtomKind
    start_char: int
    end_char: int
    text: str
    section_id: str
    section_path: list[str]
    recommendation_candidate: bool = False

    @model_validator(mode="after")
    def validate_half_open_span(self) -> "SourceAtom":
        if self.start_char < 0:
            raise ValueError("start_char must be non-negative")
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


class SourceDocument(StrictModel):
    source_doc: str
    path: Path
    text: str
    atoms: list[SourceAtom] = Field(default_factory=list)


class SemanticBatch(StrictModel):
    batch_id: str
    core_atom_ids: list[str]
    context_before_ids: list[str] = Field(default_factory=list)
    context_after_ids: list[str] = Field(default_factory=list)
    core_char_count: int
    cache_key: str | None = None


class SemanticChunkDecision(StrictModel):
    start_atom_id: str
    end_atom_id: str
    type: ChunkType
    topic_tags: list[str]
    summary: str
    decision_reason: str


class SemanticRangeDecision(StrictModel):
    start_atom_id: str
    end_atom_id: str
    reason_code: Annotated[str, Field(pattern=REASON_CODE_PATTERN)]
    decision_reason: str

    @field_validator("reason_code", mode="before")
    @classmethod
    def normalize_reason_code(cls, value: Any) -> Any:
        return _normalize_reason_code(value)


class SemanticBatchResponse(StrictModel):
    """语义模型返回的不含原文内容的 JSON 决策。"""

    chunks: list[SemanticChunkDecision] = Field(default_factory=list)
    excluded_ranges: list[SemanticRangeDecision] = Field(default_factory=list)
    review_ranges: list[SemanticRangeDecision] = Field(default_factory=list)


class ChunkBase(StrictModel):
    chunk_id: str
    source_doc: str
    section_id: str
    section_path: list[str]
    topic_tags: list[str]
    summary: str
    content: str


class FactChunk(ChunkBase):
    type: Literal["fact"]


class RecommendationChunk(ChunkBase):
    type: Literal["recommendation"]
    recommendation_levels: list[str]


class AuditRecord(StrictModel):
    audit_id: str
    run_id: str
    source_doc: str
    batch_id: str | None = None
    section_id: str | None = None
    section_path: list[str] | None = None
    start_atom_id: str | None = None
    end_atom_id: str | None = None
    source_excerpt: str
    decision: AuditDecision
    reason_code: str
    decision_reason: str
    candidate_type: ChunkType | None = None
    final_type: ChunkType | None = None
    chunk_id: str | None = None
    recommendation_candidate: bool | None = None
    recommendation_coverage_status: RecommendationCoverageStatus | None = None
    attempt_count: int = Field(ge=0, le=3)
    validation_errors: list[str]
    created_at: datetime
    parent_audit_id: str | None = None

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        if not re.fullmatch(REASON_CODE_PATTERN, value):
            raise ValueError("reason_code must be a controlled uppercase code")
        return value

    @model_validator(mode="after")
    def validate_audit_resolution(self) -> "AuditRecord":
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        if self.decision is AuditDecision.MANUALLY_RESOLVED:
            if not self.parent_audit_id:
                raise ValueError("manually_resolved requires parent_audit_id")
        elif self.parent_audit_id is not None:
            raise ValueError("parent_audit_id is reserved for manually_resolved records")
        return self
