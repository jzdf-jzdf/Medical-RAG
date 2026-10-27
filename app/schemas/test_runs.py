"""Contracts for running and inspecting question-bank tests."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TestRunCreate(BaseModel):
    bank_id: UUID
    collection_id: UUID


class TestQuestionResult(BaseModel):
    id: UUID
    question_id: UUID
    prompt: str
    question_type: Literal["qa", "single_choice"]
    options: list[dict[str, Any]] = Field(default_factory=list)
    correct_answer: str | None = None
    status: str
    answer: str | None = None
    agent_run_id: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    overall_score: float | None = None
    error: str | None = None


class TestRunSummary(BaseModel):
    id: UUID
    bank_id: UUID
    bank_name: str
    status: str
    total_questions: int
    completed_questions: int
    started_at: datetime
    finished_at: datetime | None = None


class TestRunDetail(TestRunSummary):
    results: list[TestQuestionResult] = Field(default_factory=list)
