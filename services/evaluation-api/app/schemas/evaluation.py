"""Contracts for deterministic three-layer RAG evaluation."""

from typing import Any

from pydantic import BaseModel, Field


class EvaluationRequest(BaseModel):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    expected_document_ids: list[str] = Field(default_factory=list)
    reference_answer: str | None = None
    top_k: int = Field(default=5, ge=1, le=50)


class LayerScore(BaseModel):
    score: float | None
    metrics: dict[str, float | int | None]
    issues: list[str] = Field(default_factory=list)


class EvaluationResponse(BaseModel):
    retrieval: LayerScore
    citation: LayerScore
    answer: LayerScore
    overall_score: float | None
    gate_passed: bool
