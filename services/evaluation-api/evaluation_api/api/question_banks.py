"""Persistent question-bank and question management routes."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from evaluation_api.db import get_db
from evaluation_api.models import Dataset, Question
from evaluation_api.schemas.question_bank import (
    QuestionBankCreate,
    QuestionBankDetail,
    QuestionBankSummary,
    QuestionBulkCreate,
    QuestionCreate,
    QuestionResponse,
)

router = APIRouter(tags=["question banks"])
DbSession = Annotated[AsyncSession, Depends(get_db)]


def _question_response(question: Question, bank_name: str) -> QuestionResponse:
    return QuestionResponse(
        id=question.id,
        bank_id=question.dataset_id,
        bank_name=bank_name,
        question_type=question.question_type,
        prompt=question.prompt,
        standard_answer=question.gold_answer,
        options=question.options or [],
        correct_answer=question.correct_answer,
        source=question.source,
        evidence=question.gold_evidence or [],
        metadata=question.metadata_json or {},
        created_at=question.created_at,
    )


def _bank_summary(row: object) -> QuestionBankSummary:
    bank = row[0]
    return QuestionBankSummary(
        id=bank.id,
        name=bank.name,
        description=bank.description,
        version=bank.version,
        question_count=int(row.question_count or 0),
        qa_count=int(row.qa_count or 0),
        choice_count=int(row.choice_count or 0),
        created_at=bank.created_at,
        updated_at=bank.updated_at,
    )


async def _get_bank_or_404(db: AsyncSession, bank_id: UUID) -> Dataset:
    bank = await db.get(Dataset, bank_id)
    if bank is None:
        raise HTTPException(status_code=404, detail="题库不存在")
    return bank


@router.get("/question-banks", response_model=list[QuestionBankSummary])
async def list_question_banks(db: DbSession) -> list[QuestionBankSummary]:
    statement = (
        select(
            Dataset,
            func.count(Question.id).label("question_count"),
            func.sum(case((Question.question_type == "qa", 1), else_=0)).label("qa_count"),
            func.sum(case((Question.question_type == "single_choice", 1), else_=0)).label(
                "choice_count"
            ),
        )
        .outerjoin(Question, Question.dataset_id == Dataset.id)
        .group_by(Dataset.id)
        .order_by(Dataset.updated_at.desc(), Dataset.created_at.desc())
    )
    rows = (await db.execute(statement)).all()
    return [_bank_summary(row) for row in rows]


@router.post(
    "/question-banks",
    response_model=QuestionBankSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_question_bank(payload: QuestionBankCreate, db: DbSession) -> QuestionBankSummary:
    bank = Dataset(
        name=payload.name.strip(),
        version="1.0",
        description=payload.description.strip() if payload.description else None,
    )
    db.add(bank)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="已存在同名题库") from exc
    await db.refresh(bank)
    return QuestionBankSummary(
        id=bank.id,
        name=bank.name,
        description=bank.description,
        version=bank.version,
        created_at=bank.created_at,
        updated_at=bank.updated_at,
    )


@router.get("/question-banks/{bank_id}", response_model=QuestionBankDetail)
async def get_question_bank(bank_id: UUID, db: DbSession) -> QuestionBankDetail:
    bank = await _get_bank_or_404(db, bank_id)
    questions = (
        await db.scalars(
            select(Question)
            .where(Question.dataset_id == bank_id)
            .order_by(Question.created_at.desc())
        )
    ).all()
    responses = [_question_response(question, bank.name) for question in questions]
    return QuestionBankDetail(
        id=bank.id,
        name=bank.name,
        description=bank.description,
        version=bank.version,
        question_count=len(responses),
        qa_count=sum(item.question_type == "qa" for item in responses),
        choice_count=sum(item.question_type == "single_choice" for item in responses),
        created_at=bank.created_at,
        updated_at=bank.updated_at,
        questions=responses,
    )


@router.get("/questions", response_model=list[QuestionResponse])
async def list_questions(
    db: DbSession,
    bank_id: UUID | None = None,
    question_type: Literal["qa", "single_choice"] | None = None,
    query: str | None = Query(default=None, max_length=200),
) -> list[QuestionResponse]:
    statement = select(Question, Dataset.name).join(Dataset, Dataset.id == Question.dataset_id)
    if bank_id:
        statement = statement.where(Question.dataset_id == bank_id)
    if question_type:
        statement = statement.where(Question.question_type == question_type)
    if query and query.strip():
        term = f"%{query.strip()}%"
        statement = statement.where(or_(Question.prompt.ilike(term), Question.gold_answer.ilike(term)))
    rows = (await db.execute(statement.order_by(Question.created_at.desc()))).all()
    return [_question_response(row[0], row[1]) for row in rows]


def _make_question(bank_id: UUID, payload: QuestionCreate) -> Question:
    return Question(
        dataset_id=bank_id,
        prompt=payload.prompt.strip(),
        gold_answer=payload.standard_answer.strip(),
        gold_evidence=payload.evidence,
        metadata_json=payload.metadata,
        question_type=payload.question_type,
        options=[option.model_dump() for option in payload.options],
        correct_answer=payload.correct_answer,
        source=payload.source,
    )


@router.post(
    "/question-banks/{bank_id}/questions",
    response_model=QuestionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_question(
    bank_id: UUID, payload: QuestionCreate, db: DbSession
) -> QuestionResponse:
    bank = await _get_bank_or_404(db, bank_id)
    question = _make_question(bank_id, payload)
    db.add(question)
    bank.updated_at = func.now()
    await db.commit()
    await db.refresh(question)
    return _question_response(question, bank.name)


@router.post(
    "/question-banks/{bank_id}/questions/bulk",
    response_model=list[QuestionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_questions_bulk(
    bank_id: UUID, payload: QuestionBulkCreate, db: DbSession
) -> list[QuestionResponse]:
    bank = await _get_bank_or_404(db, bank_id)
    questions = [_make_question(bank_id, item) for item in payload.questions]
    db.add_all(questions)
    bank.updated_at = func.now()
    await db.commit()
    for question in questions:
        await db.refresh(question)
    return [_question_response(question, bank.name) for question in questions]


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(question_id: UUID, db: DbSession) -> Response:
    result = await db.execute(delete(Question).where(Question.id == question_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="题目不存在")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
