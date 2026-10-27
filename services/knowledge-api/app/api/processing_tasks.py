"""Durable processing-task status, history, and retry APIs."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Document, DocumentVersion, HiddenProcessingActivity, ProcessingTask
from app.repositories.processing_tasks import TERMINAL_STATUSES, ProcessingTaskRepository
from app.schemas.dashboard import (
    ProcessingActivityRemoveRead,
    ProcessingActivityRemoveRequest,
    ProcessingTaskBulkDeleteRead,
    ProcessingTaskBulkDeleteRequest,
    ProcessingTaskRead,
)

router = APIRouter(prefix="/processing-tasks", tags=["processing-tasks"])
DOCUMENT_VERSION_PREFIX = "document-version:"


async def _delete_durable_tasks(
    task_ids: list[uuid.UUID], db: AsyncSession
) -> ProcessingTaskBulkDeleteRead:
    """Delete terminal Worker rows atomically without touching source resources."""

    unique_ids = list(dict.fromkeys(task_ids))
    repository = ProcessingTaskRepository(db)
    tasks = await repository.lock_many(unique_ids)
    found_ids = {task.id for task in tasks}
    missing_ids = [task_id for task_id in unique_ids if task_id not in found_ids]
    if missing_ids:
        await db.rollback()
        raise HTTPException(status_code=404, detail="一个或多个处理任务不存在")
    if any(task.status not in TERMINAL_STATUSES for task in tasks):
        await db.rollback()
        raise HTTPException(status_code=409, detail="只能删除已完成、失败或已取消的任务")
    await repository.delete_many(tasks)
    return ProcessingTaskBulkDeleteRead(
        deleted_ids=unique_ids,
        deleted_count=len(unique_ids),
    )


def _partition_activity_ids(
    activity_ids: list[str],
) -> tuple[list[uuid.UUID], dict[str, uuid.UUID]]:
    """Split real Worker UUIDs from supported synthetic history identifiers."""

    durable_ids: list[uuid.UUID] = []
    version_ids: dict[str, uuid.UUID] = {}
    for activity_id in dict.fromkeys(value.strip() for value in activity_ids):
        try:
            durable_ids.append(uuid.UUID(activity_id))
            continue
        except ValueError:
            pass
        if activity_id.startswith(DOCUMENT_VERSION_PREFIX):
            try:
                version_ids[activity_id] = uuid.UUID(
                    activity_id.removeprefix(DOCUMENT_VERSION_PREFIX)
                )
                continue
            except ValueError:
                pass
        raise HTTPException(status_code=422, detail="包含不支持的任务历史标识")
    return durable_ids, version_ids


async def _remove_processing_activities(
    activity_ids: list[str], db: AsyncSession
) -> ProcessingActivityRemoveRead:
    """Delete real terminal jobs and hide synthetic rows in one transaction."""

    normalized_ids = list(dict.fromkeys(value.strip() for value in activity_ids))
    durable_ids, version_ids = _partition_activity_ids(normalized_ids)
    repository = ProcessingTaskRepository(db)
    durable_tasks = await repository.lock_many(durable_ids)
    if {task.id for task in durable_tasks} != set(durable_ids):
        await db.rollback()
        raise HTTPException(status_code=404, detail="一个或多个处理任务不存在")
    if any(task.status not in TERMINAL_STATUSES for task in durable_tasks):
        await db.rollback()
        raise HTTPException(status_code=409, detail="只能删除已结束的真实任务")

    document_versions = []
    if version_ids:
        document_versions = list(
            (
                await db.scalars(
                    select(DocumentVersion)
                    .where(DocumentVersion.id.in_(version_ids.values()))
                    .with_for_update()
                )
            ).all()
        )
        if {version.id for version in document_versions} != set(version_ids.values()):
            await db.rollback()
            raise HTTPException(status_code=404, detail="一个或多个 Markdown 历史记录不存在")

    existing_hidden = set()
    if version_ids:
        existing_hidden = set(
            (
                await db.scalars(
                    select(HiddenProcessingActivity.activity_id).where(
                        HiddenProcessingActivity.activity_id.in_(version_ids)
                    )
                )
            ).all()
        )
    for activity_id in version_ids:
        if activity_id not in existing_hidden:
            db.add(HiddenProcessingActivity(activity_id=activity_id))

    if durable_tasks:
        # The repository commit includes the hidden-history tombstones staged above.
        await repository.delete_many(durable_tasks)
    else:
        await db.commit()
    return ProcessingActivityRemoveRead(
        removed_ids=normalized_ids,
        removed_count=len(normalized_ids),
    )


def _version_activity(version: DocumentVersion, document_title: str) -> ProcessingTaskRead:
    """Project a historical Markdown version into the common activity shape.

    Uploads and moves predate the durable Worker table but remain valuable audit
    events. Synthetic string IDs keep them distinct from UUID Worker jobs.
    """

    operation = {
        "upload": "Markdown 导入",
        "move": "Markdown 移动",
    }.get(version.parser, "Markdown 处理")
    processor = {
        "upload": "文件导入器",
        "move": "文献管理器",
    }.get(version.parser, version.parser)
    return ProcessingTaskRead(
        id=f"document-version:{version.id}",
        resource_id=version.document_id,
        resource_type="document",
        name=document_title,
        operation=operation,
        processor=processor,
        status="superseded" if version.status == "superseded" else "completed",
        phase="finished",
        progress=100,
        message="Markdown 版本已持久化",
        created_at=version.created_at,
        finished_at=version.created_at,
        updated_at=version.created_at,
    )


@router.get("", response_model=list[ProcessingTaskRead])
async def list_processing_tasks(
    limit: int = Query(default=100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[ProcessingTaskRead | ProcessingTask]:
    """Return one reverse-chronological processing timeline.

    Durable Worker jobs are combined with already-persisted Markdown import/move
    events. Knowledge-base current state belongs to the knowledge-base page and
    is intentionally not projected into this historical task timeline.
    """

    durable_tasks = list(
        (
            await db.scalars(
                select(ProcessingTask).order_by(ProcessingTask.created_at.desc()).limit(limit)
            )
        ).all()
    )
    # MinerU DocumentVersion rows are represented by their richer durable job,
    # while uploads and moves remain useful processing-history entries.
    version_rows = (
        await db.execute(
            select(DocumentVersion, Document.title)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.parser != "mineru")
            .order_by(DocumentVersion.created_at.desc())
            .limit(limit)
        )
    ).all()
    hidden_activity_ids = set(
        (await db.scalars(select(HiddenProcessingActivity.activity_id))).all()
    )
    items: list[ProcessingTaskRead | ProcessingTask] = [
        *durable_tasks,
        *[
            _version_activity(version, title)
            for version, title in version_rows
            if f"{DOCUMENT_VERSION_PREFIX}{version.id}" not in hidden_activity_ids
        ],
    ]
    items.sort(key=lambda item: item.created_at, reverse=True)
    return items[:limit]


@router.post("/bulk-delete", response_model=ProcessingTaskBulkDeleteRead)
async def bulk_delete_processing_tasks(
    payload: ProcessingTaskBulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
) -> ProcessingTaskBulkDeleteRead:
    """Delete multiple terminal durable tasks as one all-or-nothing operation."""

    return await _delete_durable_tasks(payload.task_ids, db)


@router.post("/remove", response_model=ProcessingActivityRemoveRead)
async def remove_processing_activities(
    payload: ProcessingActivityRemoveRequest,
    db: AsyncSession = Depends(get_db),
) -> ProcessingActivityRemoveRead:
    """Remove rows from the timeline without deleting Markdown versions or files."""

    return await _remove_processing_activities(payload.activity_ids, db)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_processing_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete one terminal durable task without deleting its generated files."""

    await _delete_durable_tasks([task_id], db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{task_id}", response_model=ProcessingTaskRead)
async def get_processing_task(
    task_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ProcessingTask:
    """Read one durable Worker task; synthetic history rows are list-only."""

    task = await ProcessingTaskRepository(db).get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="处理任务不存在")
    return task


@router.get("/{task_id}/result")
async def get_processing_task_result(
    task_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    task = await ProcessingTaskRepository(db).get(task_id)
    if task is None or task.task_type not in {"guideline_chunking", "knowledge_base_build"}:
        raise HTTPException(status_code=404, detail="指南切块任务不存在")
    if task.status == "failed" and (task.result_json or {}).get("run_id"):
        return task.result_json
    if task.status != "completed":
        raise HTTPException(status_code=409, detail=task.error_message or "指南切块尚未完成")
    return task.result_json or {}


@router.post(
    "/{task_id}/retry",
    response_model=ProcessingTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_processing_task(
    task_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ProcessingTask:
    """Requeue a failed task after enforcing retry and uniqueness invariants."""

    repository = ProcessingTaskRepository(db)
    task = await repository.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="处理任务不存在")
    # Retry is a state transition, not a second task record. Reusing the row
    # keeps attempt history, timestamps, and output/error context together.
    if task.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败任务可以重试")
    if task.attempt_count >= task.max_attempts:
        raise HTTPException(status_code=409, detail="任务已达到最大重试次数")
    if await repository.active_for_resource(task.resource_id, task_type=task.task_type):
        raise HTTPException(status_code=409, detail="该文献已有活动中的同类任务")
    return await repository.retry(task)
