import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import processing_tasks as processing_tasks_api


@pytest.mark.asyncio
async def test_delete_durable_tasks_removes_terminal_rows_atomically(monkeypatch) -> None:
    task_id = uuid.uuid4()
    task = SimpleNamespace(id=task_id, status="completed")
    repository = SimpleNamespace(
        lock_many=AsyncMock(return_value=[task]),
        delete_many=AsyncMock(),
    )
    db = SimpleNamespace(rollback=AsyncMock())
    monkeypatch.setattr(
        processing_tasks_api,
        "ProcessingTaskRepository",
        lambda _db: repository,
    )

    result = await processing_tasks_api._delete_durable_tasks([task_id], db)

    assert result.deleted_ids == [task_id]
    assert result.deleted_count == 1
    repository.delete_many.assert_awaited_once_with([task])
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_durable_tasks_rejects_active_rows(monkeypatch) -> None:
    task_id = uuid.uuid4()
    task = SimpleNamespace(id=task_id, status="running")
    repository = SimpleNamespace(
        lock_many=AsyncMock(return_value=[task]),
        delete_many=AsyncMock(),
    )
    db = SimpleNamespace(rollback=AsyncMock())
    monkeypatch.setattr(
        processing_tasks_api,
        "ProcessingTaskRepository",
        lambda _db: repository,
    )

    with pytest.raises(HTTPException) as exc_info:
        await processing_tasks_api._delete_durable_tasks([task_id], db)

    assert exc_info.value.status_code == 409
    repository.delete_many.assert_not_awaited()
    db.rollback.assert_awaited_once()


def test_partition_activity_ids_supports_document_version_history() -> None:
    task_id = uuid.uuid4()
    version_id = uuid.uuid4()
    history_id = f"document-version:{version_id}"

    durable_ids, version_ids = processing_tasks_api._partition_activity_ids(
        [str(task_id), history_id]
    )

    assert durable_ids == [task_id]
    assert version_ids == {history_id: version_id}


def test_partition_activity_ids_rejects_unknown_synthetic_history() -> None:
    with pytest.raises(HTTPException) as exc_info:
        processing_tasks_api._partition_activity_ids(["knowledge-base:not-supported"])

    assert exc_info.value.status_code == 422
