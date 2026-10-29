"""Queue, inspect, cancel, and retry saved question-bank evaluations."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from evaluation_api.db import get_db
from evaluation_api.models import Dataset, EvaluationResult, EvaluationRun, Question
from evaluation_api.schemas.test_runs import (
    TestQuestionResult,
    TestRunCreate,
    TestRunDetail,
    TestRunSummary,
)
from evaluation_api.services.platform_client import (
    PlatformClient,
    PlatformServiceError,
    get_platform_client,
)
from evaluation_api.services.run_queue import EvaluationRunQueue, get_run_queue
from evaluation_api.services.test_run_execution import agent_test_prompt, choice_score

router = APIRouter(prefix="/test-runs", tags=["question-bank tests"])
DbSession = Annotated[AsyncSession, Depends(get_db)]
AgentClient = Annotated[PlatformClient, Depends(get_platform_client)]
RunQueue = Annotated[EvaluationRunQueue, Depends(get_run_queue)]
logger = structlog.get_logger(__name__)

# Backwards-compatible private names used by existing focused tests.
_choice_score = choice_score
_agent_test_prompt = agent_test_prompt


def _summary(run: EvaluationRun, bank_name: str) -> TestRunSummary:
    return TestRunSummary(
        id=run.id,
        bank_id=run.dataset_id,
        bank_name=bank_name,
        status=run.status,
        total_questions=run.total_questions,
        completed_questions=run.completed_questions,
        attempt_count=run.attempt_count,
        cancel_requested=run.cancel_requested,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


async def _detail(db: AsyncSession, run: EvaluationRun, bank_name: str) -> TestRunDetail:
    rows = (
        await db.execute(
            select(
                EvaluationResult,
                Question.prompt,
                Question.question_type,
                Question.options,
                Question.correct_answer,
            )
            .join(Question, Question.id == EvaluationResult.question_id)
            .where(EvaluationResult.run_id == run.id)
            .order_by(Question.created_at, Question.id)
        )
    ).all()
    results = [
        TestQuestionResult(
            id=result.id,
            question_id=result.question_id,
            prompt=prompt,
            question_type=question_type,
            options=options or [],
            correct_answer=correct_answer,
            status=result.status,
            answer=result.answer,
            agent_run_id=result.agent_run_id,
            metrics=result.metrics or {},
            overall_score=result.overall_score,
            error=result.error,
        )
        for result, prompt, question_type, options, correct_answer in rows
    ]
    return TestRunDetail(**_summary(run, bank_name).model_dump(), results=results)


@router.get("", response_model=list[TestRunSummary])
async def list_test_runs(db: DbSession) -> list[TestRunSummary]:
    rows = (
        await db.execute(
            select(EvaluationRun, Dataset.name)
            .join(Dataset, Dataset.id == EvaluationRun.dataset_id)
            .order_by(EvaluationRun.started_at.desc())
            .limit(50)
        )
    ).all()
    return [_summary(run, name) for run, name in rows]


@router.get("/{run_id}", response_model=TestRunDetail)
async def get_test_run(run_id: UUID, db: DbSession) -> TestRunDetail:
    row = (
        await db.execute(
            select(EvaluationRun, Dataset.name)
            .join(Dataset, Dataset.id == EvaluationRun.dataset_id)
            .where(EvaluationRun.id == run_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="测试记录不存在")
    return await _detail(db, row[0], row[1])


@router.post("", response_model=TestRunDetail, status_code=status.HTTP_202_ACCEPTED)
async def run_question_bank_test(
    payload: TestRunCreate,
    db: DbSession,
    client: AgentClient,
    queue: RunQueue,
) -> TestRunDetail:
    bank = await db.get(Dataset, payload.bank_id)
    if bank is None:
        raise HTTPException(status_code=404, detail="题库不存在")
    try:
        knowledge_bases = await client.list_knowledge_bases()
    except PlatformServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    collection_id = str(payload.collection_id)
    if not any(str(item.get("id")) == collection_id for item in knowledge_bases):
        raise HTTPException(status_code=422, detail="所选知识库不存在或不可访问，请刷新知识库列表")
    questions = (
        await db.scalars(
            select(Question)
            .where(Question.dataset_id == bank.id)
            .order_by(Question.created_at, Question.id)
        )
    ).all()
    if not questions:
        raise HTTPException(status_code=422, detail="该题库还没有题目，无法开始测试")

    run = EvaluationRun(
        dataset_id=bank.id,
        status="queued",
        config={
            "execution_mode": "agent", "collection_id": collection_id,
            "retrieval_scoring": "reference_text_v1", "scoring_top_k": 5,
        },
        total_questions=len(questions),
        completed_questions=0,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    try:
        await queue.enqueue(run.id)
    except Exception:
        # The database is the source of truth. The dispatcher will retry while
        # Redis is unavailable or if the API dies after this commit.
        logger.exception("evaluation_initial_enqueue_failed", run_id=str(run.id))
    return await _detail(db, run, bank.name)


@router.post("/{run_id}/cancel", response_model=TestRunDetail)
async def cancel_test_run(run_id: UUID, db: DbSession) -> TestRunDetail:
    row = (
        await db.execute(
            select(EvaluationRun, Dataset.name)
            .join(Dataset, Dataset.id == EvaluationRun.dataset_id)
            .where(EvaluationRun.id == run_id)
            .with_for_update()
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="测试记录不存在")
    run, bank_name = row
    if run.status in {"completed", "partial", "failed", "cancelled"}:
        return await _detail(db, run, bank_name)
    run.cancel_requested = True
    if run.status == "queued":
        run.status = "cancelled"
        run.finished_at = datetime.now(UTC)
    else:
        run.status = "cancelling"
    await db.commit()
    await db.refresh(run)
    return await _detail(db, run, bank_name)


@router.post("/{run_id}/retry", response_model=TestRunDetail, status_code=status.HTTP_202_ACCEPTED)
async def retry_test_run(run_id: UUID, db: DbSession, queue: RunQueue) -> TestRunDetail:
    row = (
        await db.execute(
            select(EvaluationRun, Dataset.name)
            .join(Dataset, Dataset.id == EvaluationRun.dataset_id)
            .where(EvaluationRun.id == run_id)
            .with_for_update()
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="测试记录不存在")
    run, bank_name = row
    if run.status not in {"partial", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="只有失败、部分失败或已取消的测试可以重试")

    await db.execute(
        delete(EvaluationResult).where(
            EvaluationResult.run_id == run.id,
            EvaluationResult.status != "completed",
        )
    )
    completed = await db.scalar(
        select(func.count(EvaluationResult.id)).where(
            EvaluationResult.run_id == run.id,
            EvaluationResult.status == "completed",
        )
    )
    run.completed_questions = int(completed or 0)
    run.status = "queued"
    run.cancel_requested = False
    run.worker_task_id = None
    run.heartbeat_at = None
    run.last_enqueued_at = None
    run.error = None
    run.finished_at = None
    await db.commit()
    await db.refresh(run)
    try:
        await queue.enqueue(run.id)
    except Exception:
        logger.exception("evaluation_retry_enqueue_failed", run_id=str(run.id))
    return await _detail(db, run, bank_name)
