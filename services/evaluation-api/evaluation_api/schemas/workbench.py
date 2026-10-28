"""Contracts for evidence-driven question generation."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from evaluation_api.schemas.question_bank import ChoiceOption, QuestionType


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
    question_count: int = Field(default=1, ge=1, le=5)
    question_type: QuestionType = "qa"
    question_style: Literal["patient", "clinician", "professional", "public"] = "clinician"
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
    question_type: QuestionType
    question: str = Field(min_length=1)
    standard_answer: str = Field(min_length=1)
    options: list[ChoiceOption] = Field(default_factory=list, max_length=6)
    correct_answer: str | None = Field(default=None, max_length=8)
    difficulty: str = "中等"
    topic: str = ""
    rationale: str = ""
    evidence: list[EvidenceExcerpt] = Field(default_factory=list)
    source_chunks: list[ChunkItem] = Field(default_factory=list)
    agent_run_id: str
    model: str
    provider: str

    @model_validator(mode="after")
    def validate_generated_shape(self) -> "GeneratedQuestion":
        if self.question_type == "qa":
            self.options = []
            self.correct_answer = None
            return self
        if len(self.options) < 2:
            raise ValueError("选择题至少需要 2 个选项")
        keys = [item.key.strip().upper() for item in self.options]
        answer = (self.correct_answer or "").strip().upper()
        if len(set(keys)) != len(keys) or answer not in keys:
            raise ValueError("选择题选项或正确答案无效")
        self.options = [
            ChoiceOption(key=key, text=option.text)
            for key, option in zip(keys, self.options)
        ]
        self.correct_answer = answer
        return self


class GeneratedQuestionBatch(BaseModel):
    questions: list[GeneratedQuestion] = Field(min_length=1, max_length=5)
    requested_count: int = Field(ge=1, le=5)
    question_type: QuestionType
    question_style: Literal["patient", "clinician", "professional", "public"]
