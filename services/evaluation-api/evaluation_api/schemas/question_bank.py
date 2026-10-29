"""Question-bank contracts shared by manual and generated questions."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

QuestionType = Literal["qa", "single_choice"]
QuestionSource = Literal["manual", "generated"]


class ChoiceOption(BaseModel):
    key: str = Field(min_length=1, max_length=8)
    text: str = Field(min_length=1, max_length=1000)


class QuestionCreate(BaseModel):
    question_type: QuestionType = "qa"
    prompt: str = Field(min_length=1, max_length=8000)
    standard_answer: str = Field(min_length=1, max_length=16000)
    options: list[ChoiceOption] = Field(default_factory=list, max_length=6)
    correct_answer: str | None = Field(default=None, max_length=8)
    source: QuestionSource = "manual"
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_question_shape(self) -> "QuestionCreate":
        if self.question_type == "qa":
            self.options = []
            self.correct_answer = None
            return self
        if len(self.options) < 2:
            raise ValueError("选择题至少需要 2 个选项")
        keys = [option.key.strip().upper() for option in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError("选择题选项标识不能重复")
        answer = (self.correct_answer or "").strip().upper()
        if answer not in keys:
            raise ValueError("正确答案必须对应一个有效选项")
        self.options = [ChoiceOption(key=key, text=option.text) for key, option in zip(keys, self.options)]
        self.correct_answer = answer
        return self


class QuestionBulkCreate(BaseModel):
    questions: list[QuestionCreate] = Field(min_length=1, max_length=20)


class QuestionResponse(BaseModel):
    id: UUID
    bank_id: UUID
    bank_name: str
    question_type: QuestionType
    prompt: str
    standard_answer: str
    options: list[ChoiceOption] = Field(default_factory=list)
    correct_answer: str | None = None
    source: QuestionSource
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class QuestionBankCreate(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=4000)


class QuestionBankUpdate(BaseModel):
    description: str | None = Field(max_length=4000)


class QuestionBankSummary(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    version: str
    question_count: int = 0
    qa_count: int = 0
    choice_count: int = 0
    created_at: datetime
    updated_at: datetime


class QuestionBankDetail(QuestionBankSummary):
    questions: list[QuestionResponse] = Field(default_factory=list)
