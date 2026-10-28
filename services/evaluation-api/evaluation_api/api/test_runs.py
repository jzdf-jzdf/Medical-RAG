"""Run a saved question bank against the Agent and persist per-question scores."""

import re
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evaluation_api.db import get_db
from evaluation_api.models import Dataset, EvaluationResult, EvaluationRun, Question
from evaluation_api.schemas.test_runs import (
    TestQuestionResult,
    TestRunCreate,
    TestRunDetail,
    TestRunSummary,
)
from evaluation_api.services.evaluation import evaluate_layers
from evaluation_api.services.platform_client import (
    PlatformClient,
    PlatformServiceError,
    get_platform_client,
)

router = APIRouter(prefix="/test-runs", tags=["question-bank tests"])
DbSession = Annotated[AsyncSession, Depends(get_db)]
AgentClient = Annotated[PlatformClient, Depends(get_platform_client)]


def _choice_score(answer: str, question: Question) -> tuple[float, str | None]:
    clean_answer = re.sub(r"\[\d+\]", "", answer).strip().upper()
    match = re.search(
        r"(?:^|故选|应选|选项|答案|选择|选)(?:为|是)?\s*[:：]?\s*[（(\[]?([A-F])[）)\]]?"
        r"(?:选项|项)?(?:$|[，。；;、.\s])",
        clean_answer,
    )
    selected = match.group(1) if match else None
    if selected is None:
        for option in question.options or []:
            text = str(option.get("text") or "").strip().upper()
            key = str(option.get("key") or "").strip().upper()
            if text and text in clean_answer:
                selected = key
                break
    return float(selected == (question.correct_answer or "").strip().upper()), selected


def _agent_test_prompt(question: Question) -> str:
    if question.question_type == "single_choice":
        options = "\n".join(
            f"{str(option.get('key') or '').strip()}. {str(option.get('text') or '').strip()}"
            for option in question.options or []
        )
        return (
            "这是一道单选题。请依据知识库证据，从给定选项中选出唯一正确答案，"
            "只输出选项字母（例如 A），不要输出解释。\n"
            f"题目：{question.prompt}\n选项：\n{options}"
        )
    return f"这是一道问答题，请依据知识库证据直接回答。\n问题：{question.prompt}"


def _summary(run: EvaluationRun, bank_name: str) -> TestRunSummary:
    return TestRunSummary(
        id=run.id,
        bank_id=run.dataset_id,
        bank_name=bank_name,
        status=run.status,
        total_questions=run.total_questions,
        completed_questions=run.completed_questions,
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


@router.post("", response_model=TestRunDetail, status_code=status.HTTP_201_CREATED)
async def run_question_bank_test(
    payload: TestRunCreate,
    db: DbSession,
    client: AgentClient,
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
        status="running",
        config={"execution_mode": "agent", "collection_id": collection_id},
        total_questions=len(questions),
        completed_questions=0,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    successful = 0
    for question in questions:
        try:
            result = await client.ask_agent(
                content=_agent_test_prompt(question),
                title=(
                    f"题库测试：{bank.name} · "
                    f"{'单选题' if question.question_type == 'single_choice' else '问答题'} · "
                    f"{question.prompt[:80]}"
                ),
                collection_id=collection_id,
                execution_mode="agent",
            )
            answer = str(result.get("content") or "")
            citations = result.get("citations")
            citations = (
                [item for item in citations if isinstance(item, dict)]
                if isinstance(citations, list)
                else []
            )
            metadata = question.metadata_json or {}
            expected_ids = (
                [str(metadata["source_document_id"])] if metadata.get("source_document_id") else []
            )
            scores = evaluate_layers(
                answer=answer,
                citations=citations,
                expected_document_ids=expected_ids,
                reference_answer=(
                    question.correct_answer
                    if question.question_type == "single_choice"
                    else question.gold_answer
                ),
                top_k=5,
            )
            if question.question_type == "single_choice":
                choice_accuracy, selected = _choice_score(answer, question)
                scores["answer"] = {
                    "score": choice_accuracy,
                    "metrics": {"choice_accuracy": choice_accuracy, "selected_option": selected},
                    "issues": [] if choice_accuracy else ["incorrect_or_unrecognized_choice"],
                }
                layers = [
                    scores["retrieval"]["score"],
                    scores["citation"]["score"],
                    choice_accuracy,
                ]
                available = [value for value in layers if value is not None]
                scores["overall_score"] = sum(available) / len(available) if available else None
                scores["gate_passed"] = (
                    scores["citation"]["score"] >= 0.8 and choice_accuracy >= 0.5
                )
            db.add(
                EvaluationResult(
                    run_id=run.id,
                    question_id=question.id,
                    agent_run_id=str(result.get("run_id") or "") or None,
                    answer=answer,
                    metrics=scores,
                    overall_score=scores["overall_score"],
                    status="completed",
                )
            )
            successful += 1
        except PlatformServiceError as exc:
            db.add(
                EvaluationResult(
                    run_id=run.id,
                    question_id=question.id,
                    metrics={},
                    status="failed",
                    error=exc.detail,
                )
            )
        run.completed_questions += 1
        await db.commit()

    run.status = (
        "completed" if successful == len(questions) else "partial" if successful else "failed"
    )
    run.finished_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(run)
    return await _detail(db, run, bank.name)
