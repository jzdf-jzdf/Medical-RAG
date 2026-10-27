"""Database queue operations for durable processing tasks."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProcessingTask

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class ProcessingTaskRepository:
    """Own all state transitions for the PostgreSQL-backed task queue.

    API handlers and the Worker intentionally use this repository instead of
    assigning task fields directly. This keeps every transition transactional
    and makes the queue invariants visible in one place.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, task_id: uuid.UUID) -> ProcessingTask | None:
        """Return a task including historical terminal rows."""

        return await self.session.get(ProcessingTask, task_id)

    async def lock_many(self, task_ids: list[uuid.UUID]) -> list[ProcessingTask]:
        """Lock durable task rows while a destructive operation validates them."""

        if not task_ids:
            return []
        return list(
            (
                await self.session.scalars(
                    select(ProcessingTask)
                    .where(ProcessingTask.id.in_(task_ids))
                    .with_for_update()
                )
            ).all()
        )

    async def delete_many(self, tasks: list[ProcessingTask]) -> None:
        """Delete already-validated rows in one transaction."""

        for task in tasks:
            await self.session.delete(task)
        await self.session.commit()

    async def active_for_resource(
        self,
        resource_id: uuid.UUID,
        *,
        task_type: str | None = None,
    ) -> ProcessingTask | None:
        """Find the newest live task protecting a domain resource.

        This lookup is used both for duplicate-enqueue prevention and for
        rejecting attachment mutations while a Worker reads/writes the files.
        """

        statement = select(ProcessingTask).where(
            ProcessingTask.resource_id == resource_id,
            ProcessingTask.status.in_(ACTIVE_STATUSES),
        )
        if task_type:
            statement = statement.where(ProcessingTask.task_type == task_type)
        return await self.session.scalar(statement.order_by(ProcessingTask.created_at.desc()))

    async def enqueue_pdf_conversion(
        self,
        *,
        document_id: uuid.UUID,
        document_name: str,
        replace_existing: bool,
    ) -> tuple[ProcessingTask, bool]:
        """Create one active conversion per document, returning an existing job if raced."""

        # Fast path for ordinary requests. The database partial unique index
        # below remains authoritative if concurrent requests both pass here.
        existing = await self.active_for_resource(
            document_id, task_type="pdf_to_markdown"
        )
        if existing:
            return existing, False

        # Only durable identifiers and user-confirmed options enter the queue;
        # filesystem paths are resolved again by the Worker at execution time.
        task = ProcessingTask(
            task_type="pdf_to_markdown",
            resource_type="document",
            resource_id=document_id,
            resource_name=document_name,
            status="queued",
            phase="queued",
            progress=0,
            message="等待后台 Worker 领取任务",
            payload_json={"replace_existing": replace_existing},
            result_json={},
            max_attempts=2,
        )
        self.session.add(task)
        try:
            await self.session.commit()
        except IntegrityError:
            # The partial unique index is the final duplicate guard when two
            # browser requests enqueue the same document concurrently.
            await self.session.rollback()
            existing = await self.active_for_resource(
                document_id, task_type="pdf_to_markdown"
            )
            if existing:
                return existing, False
            raise
        await self.session.refresh(task)
        return task, True

    async def enqueue(
        self,
        *,
        task_type: str,
        resource_type: str,
        resource_id: uuid.UUID,
        resource_name: str,
        payload: dict | None = None,
        max_attempts: int = 2,
    ) -> tuple[ProcessingTask, bool]:
        """Create one durable generic job per task/resource pair."""

        existing = await self.active_for_resource(resource_id, task_type=task_type)
        if existing:
            return existing, False
        task = ProcessingTask(
            task_type=task_type,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            status="queued",
            phase="queued",
            progress=0,
            message="等待后台 Worker 领取任务",
            payload_json=payload or {},
            result_json={},
            max_attempts=max_attempts,
        )
        self.session.add(task)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.active_for_resource(resource_id, task_type=task_type)
            if existing:
                return existing, False
            raise
        await self.session.refresh(task)
        return task, True

    async def claim_next(self, worker_id: str) -> ProcessingTask | None:
        """Atomically claim the oldest queued task without blocking peers.

        ``FOR UPDATE SKIP LOCKED`` turns PostgreSQL rows into short-lived queue
        leases: concurrent Worker processes skip a row already being claimed,
        so exactly one of them can commit the queued -> running transition.
        """

        statement = (
            select(ProcessingTask)
            .where(ProcessingTask.status == "queued")
            .order_by(ProcessingTask.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        # The row lock remains held until commit. Rolling back the empty query
        # closes the read transaction before the Worker sleeps.
        task = await self.session.scalar(statement)
        if task is None:
            await self.session.rollback()
            return None
        now = datetime.now(UTC)
        task.status = "running"
        task.phase = "preparing"
        task.progress = 5
        task.message = {
            "pdf_to_markdown": "正在准备 PDF 输入文件",
            "knowledge_base_build": "正在准备文献分块与向量构建",
            "embedding_model_download": "正在准备 MedCPT 模型下载",
            "evidence_extraction": "正在准备证据候选抽取",
        }.get(task.task_type, "正在准备后台任务")
        task.error_message = None
        task.claimed_by = worker_id
        task.heartbeat_at = now
        task.started_at = task.started_at or now
        task.finished_at = None
        # An attempt starts only after the Worker owns the row. Recovery uses
        # this count to decide between automatic requeue and terminal failure.
        task.attempt_count += 1
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def update_running(
        self,
        task_id: uuid.UUID,
        *,
        phase: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        heartbeat_only: bool = False,
    ) -> None:
        """Refresh a running task's lease and optionally its visible progress.

        The status predicate prevents a late MinerU callback from overwriting a
        task that another recovery path has already moved to a terminal state.
        """

        values: dict[str, object] = {"heartbeat_at": datetime.now(UTC)}
        if not heartbeat_only:
            if phase is not None:
                values["phase"] = phase
            # A None progress deliberately means the current MinerU phase is
            # indeterminate; do not replace it with a fabricated percentage.
            values["progress"] = progress
            if message is not None:
                values["message"] = message
        await self.session.execute(
            update(ProcessingTask)
            .where(
                ProcessingTask.id == task_id,
                ProcessingTask.status == "running",
            )
            .values(**values)
        )
        await self.session.commit()

    async def complete(
        self, task_id: uuid.UUID, result: dict, message: str = "处理已完成"
    ) -> None:
        """Persist a successful terminal state and the generated resource IDs."""

        now = datetime.now(UTC)
        await self.session.execute(
            update(ProcessingTask)
            .where(ProcessingTask.id == task_id)
            .values(
                status="completed",
                phase="finished",
                progress=100,
                message=message,
                result_json=result,
                error_message=None,
                claimed_by=None,
                heartbeat_at=now,
                finished_at=now,
            )
        )
        await self.session.commit()

    async def fail(self, task_id: uuid.UUID, message: str) -> None:
        """Persist a failed terminal state while bounding database error text."""

        now = datetime.now(UTC)
        await self.session.execute(
            update(ProcessingTask)
            .where(ProcessingTask.id == task_id)
            .values(
                status="failed",
                phase="failed",
                progress=None,
                message="处理失败",
                error_message=message[:4000],
                claimed_by=None,
                heartbeat_at=now,
                finished_at=now,
            )
        )
        await self.session.commit()

    async def release_for_retry(self, task_id: uuid.UUID, message: str) -> None:
        """Return an interrupted in-process job to the queue during shutdown."""

        await self.session.execute(
            update(ProcessingTask)
            .where(
                ProcessingTask.id == task_id,
                ProcessingTask.status == "running",
            )
            .values(
                status="queued",
                phase="queued",
                progress=0,
                message=message,
                claimed_by=None,
                heartbeat_at=None,
                finished_at=None,
            )
        )
        await self.session.commit()

    async def retry(self, task: ProcessingTask) -> ProcessingTask:
        """Reset a validated failed task so a Worker may claim it again."""

        task.status = "queued"
        task.phase = "queued"
        task.progress = 0
        task.message = "等待后台 Worker 重试"
        task.error_message = None
        task.claimed_by = None
        task.heartbeat_at = None
        task.finished_at = None
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def recover_stale(self, stale_seconds: int) -> int:
        """Recover jobs whose Worker heartbeat expired after a crash or reload.

        Recovery uses two bulk updates in one transaction. Exhausted jobs become
        visibly failed; jobs with remaining attempts return to FIFO ordering.
        This guarantees that no stale ``running`` row remains stuck forever.
        """

        cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
        # A stale job that already consumed all attempts must terminate instead
        # of remaining forever in the running state.
        exhausted = await self.session.execute(
            update(ProcessingTask)
            .where(
                ProcessingTask.status == "running",
                ProcessingTask.heartbeat_at < cutoff,
                ProcessingTask.attempt_count >= ProcessingTask.max_attempts,
            )
            .values(
                status="failed",
                phase="failed",
                progress=None,
                message="Worker 中断且已达到最大重试次数",
                error_message="Worker 心跳超时，自动重试次数已耗尽",
                claimed_by=None,
                heartbeat_at=None,
                finished_at=datetime.now(UTC),
            )
        )
        # Only tasks with another attempt available are made claimable again.
        requeued = await self.session.execute(
            update(ProcessingTask)
            .where(
                ProcessingTask.status == "running",
                ProcessingTask.heartbeat_at < cutoff,
                ProcessingTask.attempt_count < ProcessingTask.max_attempts,
            )
            .values(
                status="queued",
                phase="queued",
                progress=0,
                message="检测到 Worker 中断，任务已重新排队",
                claimed_by=None,
                heartbeat_at=None,
            )
        )
        await self.session.commit()
        return int(exhausted.rowcount or 0) + int(requeued.rowcount or 0)
