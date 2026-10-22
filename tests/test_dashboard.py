import uuid
from datetime import UTC, datetime

from app.api.processing_tasks import _version_activity
from app.models import DocumentVersion
from app.services.task_worker import progress_from_output


def test_uploaded_version_is_exposed_as_completed_real_activity() -> None:
    now = datetime.now(UTC)
    version = DocumentVersion(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        version=1,
        parser="upload",
        parser_version="1",
        status="ready",
        markdown_path="documents/example.md",
        created_at=now,
    )

    activity = _version_activity(version, "脑出血指南")

    assert activity.operation == "Markdown 导入"
    assert activity.processor == "文件导入器"
    assert activity.status == "completed"
    assert activity.progress == 100


def test_worker_extracts_real_progress_without_fabricating_unknown_values() -> None:
    assert progress_from_output("processing 50%") == 50
    assert progress_from_output("page 3 / 4") == 70
    assert progress_from_output("loading model weights") is None
