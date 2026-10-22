"""Single-concurrency background Worker backed entirely by PostgreSQL."""

import asyncio
import logging
import re
import socket
import time
import uuid

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import EmbeddingModel
from app.repositories.processing_tasks import ProcessingTaskRepository
from app.services.embeddings import EmbeddingError, download_medcpt_model
from app.services.evidence_extraction import extract_collection_evidence
from app.services.indexing import build_collection_index, mark_collection_failed
from app.services.mineru import MinerUError
from app.services.pdf_conversion import PdfConversionError, convert_document_pdf

logger = logging.getLogger(__name__)
settings = get_settings()
# MinerU versions may report either a literal percentage or a page counter.
# These deliberately strict patterns avoid interpreting unrelated log numbers
# (model versions, dimensions, timestamps) as progress.
PERCENT_PATTERN = re.compile(r"(?<!\d)(100|[1-9]?\d)\s*%")
PAGE_PATTERN = re.compile(r"(?<!\d)(\d{1,6})\s*/\s*(\d{1,6})(?!\d)")


def progress_from_output(line: str) -> int | None:
    """Map reliable MinerU output into the conversion portion of the progress bar.

    Queue preparation owns 0–10% and database persistence owns 90–100%, so
    external parser output is compressed into 10–90%. Returning ``None`` is an
    intentional indeterminate state; the UI must not present guessed progress.
    """

    percent_match = PERCENT_PATTERN.search(line)
    if percent_match:
        raw = int(percent_match.group(1))
        return min(90, max(10, 10 + round(raw * 0.8)))
    page_match = PAGE_PATTERN.search(line)
    if page_match:
        current, total = (int(value) for value in page_match.groups())
        if total > 0 and current <= total:
            return min(90, max(10, 10 + round(current / total * 80)))
    return None


class ProcessingTaskWorker:
    """Execute durable tasks using one local, single-concurrency consumer.

    The Worker contains no authoritative in-memory queue. It repeatedly claims
    PostgreSQL rows, runs the matching service, and records all observable state
    back to the same row. The single-concurrency loop protects local CPU/GPU and
    memory; PostgreSQL locking still makes additional app processes safe.
    """

    def __init__(self) -> None:
        # A random suffix distinguishes app reloads on the same host in logs and
        # in claimed_by without requiring a separate Worker registration table.
        self.worker_id = f"{socket.gethostname()}:{uuid.uuid4()}"
        self._runner: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._current_task_id: uuid.UUID | None = None

    async def start(self) -> None:
        """Recover abandoned leases, then start the consumer task once."""

        if self._runner is not None:
            return
        async with SessionLocal() as db:
            await ProcessingTaskRepository(db).recover_stale(settings.task_stale_seconds)
        self._runner = asyncio.create_task(self._run(), name="processing-task-worker")

    async def stop(self) -> None:
        """Cancel local execution and wait until the job is safely requeued."""

        self._stop_event.set()
        if self._runner is None:
            return
        self._runner.cancel()
        await asyncio.gather(self._runner, return_exceptions=True)
        self._runner = None

    async def _run(self) -> None:
        """Poll for FIFO work and periodically sweep expired Worker leases."""

        last_recovery = time.monotonic()
        try:
            while not self._stop_event.is_set():
                # Startup recovery is not enough for long-running services: a
                # peer Worker can disappear at any time, so sweep at half TTL.
                if time.monotonic() - last_recovery >= settings.task_stale_seconds / 2:
                    async with SessionLocal() as db:
                        await ProcessingTaskRepository(db).recover_stale(
                            settings.task_stale_seconds
                        )
                    last_recovery = time.monotonic()

                async with SessionLocal() as db:
                    task = await ProcessingTaskRepository(db).claim_next(self.worker_id)
                if task is None:
                    # Waiting on an Event rather than plain sleep lets shutdown
                    # wake the idle Worker immediately.
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=settings.task_worker_poll_seconds,
                        )
                    except TimeoutError:
                        pass
                    continue

                self._current_task_id = task.id
                await self._execute(task.id)
                self._current_task_id = None
        except asyncio.CancelledError:
            # App shutdown is not a conversion failure. Put the claimed row back
            # into the durable queue so the next process can resume from scratch.
            if self._current_task_id:
                async with SessionLocal() as db:
                    await ProcessingTaskRepository(db).release_for_retry(
                        self._current_task_id,
                        "服务已停止，任务将在下次启动后继续",
                    )
            raise

    async def _execute(self, task_id: uuid.UUID) -> None:
        """Execute one claimed task and force every outcome into durable state."""

        async with SessionLocal() as db:
            task = await ProcessingTaskRepository(db).get(task_id)
            if task is None:
                return
            task_type = task.task_type
            resource_id = task.resource_id
            payload = dict(task.payload_json or {})

        heartbeat = asyncio.create_task(self._heartbeat(task_id))
        try:
            if task_type == "pdf_to_markdown":
                result = await self._execute_pdf(task_id, resource_id, payload)
                message = "Markdown 已生成并保存"
            elif task_type == "knowledge_base_build":
                async with SessionLocal() as db:
                    result = await build_collection_index(
                        db,
                        resource_id,
                        on_progress=lambda phase, progress, message: self._update(
                            task_id,
                            phase=phase,
                            progress=progress,
                            message=message,
                        ),
                    )
                message = "MedCPT 向量索引已构建"
            elif task_type == "embedding_model_download":
                async with SessionLocal() as db:
                    model = await db.get(EmbeddingModel, resource_id)
                    if model is None:
                        raise EmbeddingError("嵌入模型配置不存在")
                    result = await download_medcpt_model(
                        model,
                        on_progress=lambda phase, progress, message: self._update(
                            task_id,
                            phase=phase,
                            progress=progress,
                            message=message,
                        ),
                    )
                message = "MedCPT 查询与文献编码器已下载"
            elif task_type == "evidence_extraction":
                async with SessionLocal() as db:
                    result = await extract_collection_evidence(
                        db,
                        resource_id,
                        on_progress=lambda phase, progress, message: self._update(
                            task_id,
                            phase=phase,
                            progress=progress,
                            message=message,
                        ),
                    )
                message = "证据候选已生成，等待人工审核"
            else:
                raise ValueError(f"不支持的后台任务类型：{task_type}")
            async with SessionLocal() as db:
                await ProcessingTaskRepository(db).complete(task_id, result, message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Unexpected processing task failure", extra={"task_id": str(task_id)})
            async with SessionLocal() as db:
                if task_type == "knowledge_base_build":
                    await mark_collection_failed(db, resource_id, str(exc))
                await ProcessingTaskRepository(db).fail(
                    task_id,
                    str(exc)
                    if isinstance(exc, (MinerUError, PdfConversionError, EmbeddingError, ValueError))
                    else f"后台处理发生未预期错误：{exc}",
                )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _execute_pdf(
        self, task_id: uuid.UUID, document_id: uuid.UUID, payload: dict
    ) -> dict[str, str]:
        """Execute MinerU conversion while persisting trustworthy progress."""

        await self._update(
            task_id, phase="parsing", progress=None, message="MinerU 正在解析 PDF"
        )
        last_progress: int | None = None
        last_update_at = 0.0

        async def on_output(line: str) -> None:
            nonlocal last_progress, last_update_at
            progress = progress_from_output(line)
            now = time.monotonic()
            if progress is None or (progress == last_progress and now - last_update_at < 2):
                return
            last_progress = progress
            last_update_at = now
            await self._update(
                task_id,
                phase="parsing",
                progress=progress,
                message=f"MinerU 正在解析 PDF（{progress}%）",
            )

        async def on_persisting() -> None:
            await self._update(
                task_id,
                phase="persisting",
                progress=95,
                message="正在保存 Markdown 和文献版本",
            )

        async with SessionLocal() as db:
            converted = await convert_document_pdf(
                db,
                document_id,
                replace_existing=bool(payload.get("replace_existing", False)),
                on_output=on_output,
                on_persisting=on_persisting,
            )
        return {
            "document_id": str(converted.document_id),
            "attachment_id": str(converted.attachment_id),
            "storage_path": converted.storage_path,
        }

    async def _update(
        self,
        task_id: uuid.UUID,
        *,
        phase: str,
        progress: int | None,
        message: str,
    ) -> None:
        """Write one progress update using a short-lived database session."""

        async with SessionLocal() as db:
            await ProcessingTaskRepository(db).update_running(
                task_id,
                phase=phase,
                progress=progress,
                message=message,
            )

    async def _heartbeat(self, task_id: uuid.UUID) -> None:
        """Renew the task lease even when MinerU emits no console output."""

        while True:
            await asyncio.sleep(settings.task_heartbeat_seconds)
            async with SessionLocal() as db:
                await ProcessingTaskRepository(db).update_running(
                    task_id, heartbeat_only=True
                )
