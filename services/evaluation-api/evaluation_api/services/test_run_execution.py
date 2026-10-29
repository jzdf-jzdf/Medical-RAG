"""Idempotent, resumable execution of one saved evaluation run."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evaluation_api.core.config import get_settings
from evaluation_api.db import SessionLocal
from evaluation_api.models import Dataset, EvaluationResult, EvaluationRun, Question
from evaluation_api.services.evaluation import evaluate_layers
from evaluation_api.services.platform_client import (
    PlatformClient,
    PlatformServiceError,
    extract_retrieved_chunks,
)

TERMINAL_RUN_STATUSES = {"completed", "partial", "failed", "cancelled"}


def choice_score(answer: str, question: Question) -> tuple[float, str | None]:
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


def agent_test_prompt(question: Question) -> str:
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


async def _claim_run(
    db: AsyncSession,
    run_id: UUID,
    task_id: str,
) -> tuple[EvaluationRun, Dataset] | None:
    row = (
        await db.execute(
            select(EvaluationRun, Dataset)
            .join(Dataset, Dataset.id == EvaluationRun.dataset_id)
            .where(EvaluationRun.id == run_id)
            .with_for_update()
        )
    ).first()
    if row is None:
        return None
    run, bank = row
    if run.status in TERMINAL_RUN_STATUSES:
        return None
    if run.cancel_requested:
        run.status = "cancelled"
        run.finished_at = datetime.now(UTC)
        run.heartbeat_at = run.finished_at
        await db.commit()
        return None

    settings = get_settings()
    stale_before = datetime.now(UTC) - timedelta(seconds=settings.evaluation_stale_seconds)
    if (
        run.status == "running"
        and run.worker_task_id != task_id
        and run.heartbeat_at is not None
        and run.heartbeat_at > stale_before
    ):
        return None

    run.status = "running"
    run.worker_task_id = task_id
    run.attempt_count += 1
    run.heartbeat_at = datetime.now(UTC)
    run.error = None
    await db.commit()
    return run, bank


async def _finish_cancelled(db: AsyncSession, run: EvaluationRun) -> None:
    run.status = "cancelled"
    run.finished_at = datetime.now(UTC)
    run.heartbeat_at = run.finished_at
    run.worker_task_id = None
    await db.commit()


async def execute_evaluation_run(
    run_id: UUID,
    task_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
    client: PlatformClient | None = None,
) -> None:
    """Run missing questions only, so a redelivered task resumes safely."""

    platform = client or PlatformClient(get_settings())
    async with session_factory() as db:
        claimed = await _claim_run(db, run_id, task_id)
        if claimed is None:
            return
        run, bank = claimed
        questions = (
            await db.scalars(
                select(Question)
                .where(Question.dataset_id == run.dataset_id)
                .order_by(Question.created_at, Question.id)
            )
        ).all()
        existing_ids = set(
            await db.scalars(
                select(EvaluationResult.question_id).where(EvaluationResult.run_id == run.id)
            )
        )
        run.completed_questions = len(existing_ids)
        await db.commit()

        for question in questions:
            if question.id in existing_ids:
                continue
            await db.refresh(run, attribute_names=["cancel_requested"])
            if run.cancel_requested:
                await _finish_cancelled(db, run)
                return
            run.heartbeat_at = datetime.now(UTC)
            await db.commit()

            try:
                result = await platform.ask_agent(
                    content=agent_test_prompt(question),
                    title=(
                        f"题库测试：{bank.name} · "
                        f"{'单选题' if question.question_type == 'single_choice' else '问答题'} · "
                        f"{question.prompt[:80]}"
                    ),
                    collection_id=str(run.config["collection_id"]),
                    execution_mode="agent",
                )
                answer = str(result.get("content") or "")
                raw_citations = result.get("citations")
                citations = (
                    [item for item in raw_citations if isinstance(item, dict)]
                    if isinstance(raw_citations, list)
                    else []
                )
                metadata = question.metadata_json or {}
                retrieved_chunks, retrieval_source = extract_retrieved_chunks(result)
                expected_ids = (
                    [str(metadata["source_document_id"])]
                    if metadata.get("source_document_id")
                    else []
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
                    top_k=int(run.config.get("scoring_top_k", 5)),
                    reference_chunks=question.gold_evidence or [],
                    retrieved_chunks=retrieved_chunks,
                )
                scores["retrieval"]["source"] = retrieval_source
                if question.question_type == "single_choice":
                    choice_accuracy, selected = choice_score(answer, question)
                    scores["answer"] = {
                        "score": choice_accuracy,
                        "metrics": {
                            "choice_accuracy": choice_accuracy,
                            "selected_option": selected,
                        },
                        "issues": [] if choice_accuracy else ["incorrect_or_unrecognized_choice"],
                    }
                    layers = [
                        scores["retrieval"]["score"],
                        scores["citation"]["score"],
                        choice_accuracy,
                    ]
                    available = [value for value in layers if value is not None]
                    scores["overall_score"] = (
                        sum(available) / len(available) if available else None
                    )
                    scores["gate_passed"] = (
                        scores["citation"]["score"] >= 0.8
                        and choice_accuracy >= 0.5
                        and (
                            scores["retrieval"]["score"] is None
                            or scores["retrieval"]["score"] >= 0.5
                        )
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
            run.heartbeat_at = datetime.now(UTC)
            await db.commit()

        statuses = list(
            await db.scalars(
                select(EvaluationResult.status).where(EvaluationResult.run_id == run.id)
            )
        )
        successful = statuses.count("completed")
        run.completed_questions = len(statuses)
        run.status = (
            "completed"
            if successful == len(questions)
            else "partial"
            if successful
            else "failed"
        )
        run.finished_at = datetime.now(UTC)
        run.heartbeat_at = run.finished_at
        run.worker_task_id = None
        await db.commit()


async def mark_run_interrupted(run_id: UUID, error: str, *, terminal: bool) -> None:
    async with SessionLocal() as db:
        run = await db.get(EvaluationRun, run_id, with_for_update=True)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return
        run.status = "failed" if terminal else "queued"
        run.error = error[:4000]
        run.worker_task_id = None
        run.heartbeat_at = datetime.now(UTC)
        run.last_enqueued_at = None if not terminal else run.last_enqueued_at
        if terminal:
            run.finished_at = datetime.now(UTC)
        await db.commit()
