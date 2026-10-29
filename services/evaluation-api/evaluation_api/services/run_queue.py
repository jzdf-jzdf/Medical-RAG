"""Redis/Celery dispatch and database-backed recovery for evaluation runs."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import or_, select

from evaluation_api.celery_app import celery_app
from evaluation_api.core.config import Settings, get_settings
from evaluation_api.db import SessionLocal
from evaluation_api.models import EvaluationRun

logger = structlog.get_logger(__name__)


class EvaluationRunQueue:
    async def enqueue(self, run_id: UUID) -> str:
        result = await asyncio.to_thread(
            celery_app.send_task,
            "evaluation.execute_test_run",
            args=[str(run_id)],
            retry=False,
        )
        async with SessionLocal() as db:
            run = await db.get(EvaluationRun, run_id)
            if run is not None:
                run.last_enqueued_at = datetime.now(UTC)
                await db.commit()
        return str(result.id)


def get_run_queue() -> EvaluationRunQueue:
    return EvaluationRunQueue()


class EvaluationRunDispatcher:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.queue = EvaluationRunQueue()
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        await self.dispatch_once()
        self._task = asyncio.create_task(self._run(), name="evaluation-run-dispatcher")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.settings.evaluation_dispatch_interval_seconds,
                )
            except TimeoutError:
                try:
                    await self.dispatch_once()
                except Exception:
                    logger.exception("evaluation_dispatch_failed")

    async def dispatch_once(self) -> None:
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=self.settings.evaluation_stale_seconds)
        requeue_before = now - timedelta(seconds=self.settings.evaluation_requeue_seconds)
        async with SessionLocal() as db:
            stale_runs = list(
                await db.scalars(
                    select(EvaluationRun)
                    .where(
                        EvaluationRun.status.in_(["running", "cancelling"]),
                        EvaluationRun.heartbeat_at < stale_before,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(20)
                )
            )
            for run in stale_runs:
                run.status = "cancelled" if run.cancel_requested else "queued"
                run.worker_task_id = None
                run.last_enqueued_at = None
                run.error = (
                    "Worker heartbeat expired while cancellation was pending"
                    if run.cancel_requested
                    else "Worker heartbeat expired; run was requeued"
                )
                if run.cancel_requested:
                    run.finished_at = now
            await db.commit()

            run_ids = list(
                await db.scalars(
                    select(EvaluationRun.id)
                    .where(
                        EvaluationRun.status == "queued",
                        or_(
                            EvaluationRun.last_enqueued_at.is_(None),
                            EvaluationRun.last_enqueued_at < requeue_before,
                        ),
                    )
                    .order_by(EvaluationRun.started_at)
                    .limit(20)
                )
            )

        for run_id in run_ids:
            try:
                await self.queue.enqueue(run_id)
            except Exception:
                logger.exception("evaluation_enqueue_failed", run_id=str(run_id))
