"""Celery task entry points."""

import asyncio
from uuid import UUID

from evaluation_api.celery_app import celery_app
from evaluation_api.core.config import get_settings
from evaluation_api.db import engine
from evaluation_api.services.test_run_execution import (
    execute_evaluation_run,
    mark_run_interrupted,
)


async def _execute(run_id: UUID, task_id: str) -> None:
    try:
        await execute_evaluation_run(run_id, task_id)
    finally:
        await engine.dispose()


@celery_app.task(bind=True, name="evaluation.execute_test_run")
def execute_test_run_task(self, run_id: str) -> None:
    settings = get_settings()
    run_uuid = UUID(run_id)
    try:
        asyncio.run(_execute(run_uuid, str(self.request.id)))
    except Exception as exc:
        terminal = self.request.retries >= settings.evaluation_task_max_retries
        asyncio.run(mark_run_interrupted(run_uuid, str(exc), terminal=terminal))
        if terminal:
            raise
        raise self.retry(exc=exc, countdown=min(60, 2 ** (self.request.retries + 1)))
