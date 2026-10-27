import uuid
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile
from pypdf import PdfWriter
from pypdf.errors import DependencyError

from app.api import documents as documents_api
from app.api.documents import (
    StoredUpload,
    _document_payload,
    _entry_title_from_filename,
    _markdown_metadata,
    _pdf_metadata,
    _store_file,
    _title_from_filename,
    delete_document,
    settings,
)


def test_title_from_filename_normalizes_separators() -> None:
    assert _title_from_filename("2024_ICH-guideline.pdf") == "2024 ICH guideline"


def test_title_from_filename_has_unnamed_fallback() -> None:
    assert _title_from_filename("_.pdf") == "未命名文献"


def test_entry_title_preserves_uploaded_filename_stem() -> None:
    assert _entry_title_from_filename("2024_ICH-guideline.pdf") == "2024_ICH-guideline"


def test_document_payload_does_not_replace_filename_with_embedded_title() -> None:
    upload = StoredUpload(
        original_name="急性脑出血指南.pdf",
        storage_path="attachments/ab/file.pdf",
        digest="ab",
        media_type="application/pdf",
        size_bytes=128,
        created_new=True,
    )

    payload = _document_payload(
        upload,
        {
            "title": "错误的 PDF 内嵌标题",
            "authors": ["张医生"],
            "metadata": {"metadata_source": "pdf"},
        },
    )

    assert payload.title == "急性脑出血指南"
    assert payload.authors == ["张医生"]


@pytest.mark.asyncio
async def test_store_file_uses_content_addressed_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    upload = UploadFile(filename="guideline.pdf", file=BytesIO(b"%PDF-1.4\nmedical evidence"))

    stored = await _store_file(upload)

    stored_path = tmp_path / stored.storage_path
    assert stored.original_name == "guideline.pdf"
    assert stored.size_bytes == stored_path.stat().st_size
    assert stored_path.name == f"{stored.digest}.pdf"


@pytest.mark.asyncio
async def test_store_file_accepts_markdown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    upload = UploadFile(filename="notes.md", file=BytesIO(b"# Evidence notes"))

    stored = await _store_file(upload)

    assert stored.media_type.startswith("text/markdown")
    assert (tmp_path / stored.storage_path).suffix == ".md"


def test_pdf_metadata_reads_embedded_fields(tmp_path) -> None:
    path = tmp_path / "article.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata(
        {"/Title": "ICH Evidence", "/Author": "Li Ming; Wang Lei", "/Subject": "Review 2025"}
    )
    with path.open("wb") as output:
        writer.write(output)

    metadata = _pdf_metadata(path, path.name)

    assert metadata["title"] == "ICH Evidence"
    assert metadata["authors"] == ["Li Ming", "Wang Lei"]
    assert metadata["publication_year"] == 2025


def test_pdf_metadata_falls_back_when_optional_decoder_fails(tmp_path, monkeypatch) -> None:
    path = tmp_path / "encrypted-guideline.pdf"
    path.write_bytes(b"%PDF-1.7")

    def fail_reader(_path):
        raise DependencyError("optional AES decoder unavailable")

    monkeypatch.setattr(documents_api, "PdfReader", fail_reader)

    metadata = _pdf_metadata(path, path.name)

    assert metadata["title"] == "encrypted guideline"
    assert metadata["metadata"]["metadata_extracted"] is False


@pytest.mark.asyncio
async def test_delete_document_rejects_knowledge_base_member(monkeypatch) -> None:
    document_id = uuid.uuid4()
    repository = SimpleNamespace(
        get_for_update=AsyncMock(return_value=SimpleNamespace(files=[])),
        knowledge_base_names=AsyncMock(return_value=["脑出血指南库"]),
        delete=AsyncMock(),
    )
    task_repository = SimpleNamespace(active_for_resource=AsyncMock(return_value=None))
    monkeypatch.setattr(documents_api, "DocumentRepository", lambda _db: repository)
    monkeypatch.setattr(
        documents_api, "ProcessingTaskRepository", lambda _db: task_repository
    )

    with pytest.raises(HTTPException) as exc_info:
        await delete_document(document_id, db=SimpleNamespace())

    assert exc_info.value.status_code == 409
    assert "脑出血指南库" in exc_info.value.detail
    repository.delete.assert_not_awaited()


def test_markdown_metadata_reads_frontmatter_and_heading(tmp_path) -> None:
    path = tmp_path / "article.md"
    path.write_text(
        "---\ntitle: ICH Review\nauthors: Li Ming; Wang Lei\nyear: 2024\n---\n# Ignored\n",
        encoding="utf-8",
    )

    metadata = _markdown_metadata(path, path.name)

    assert metadata["title"] == "ICH Review"
    assert metadata["authors"] == ["Li Ming", "Wang Lei"]
    assert metadata["publication_year"] == 2024
