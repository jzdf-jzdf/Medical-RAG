from io import BytesIO

import pytest
from fastapi import UploadFile
from pypdf import PdfWriter

from app.api.documents import (
    _markdown_metadata,
    _pdf_metadata,
    _store_file,
    _title_from_filename,
    settings,
)


def test_title_from_filename_normalizes_separators() -> None:
    assert _title_from_filename("2024_ICH-guideline.pdf") == "2024 ICH guideline"


def test_title_from_filename_has_unnamed_fallback() -> None:
    assert _title_from_filename("_.pdf") == "未命名文献"


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
    writer.add_metadata({"/Title": "ICH Evidence", "/Author": "Li Ming; Wang Lei", "/Subject": "Review 2025"})
    with path.open("wb") as output:
        writer.write(output)

    metadata = _pdf_metadata(path, path.name)

    assert metadata["title"] == "ICH Evidence"
    assert metadata["authors"] == ["Li Ming", "Wang Lei"]
    assert metadata["publication_year"] == 2025


def test_markdown_metadata_reads_frontmatter_and_heading(tmp_path) -> None:
    path = tmp_path / "article.md"
    path.write_text("---\ntitle: ICH Review\nauthors: Li Ming; Wang Lei\nyear: 2024\n---\n# Ignored\n", encoding="utf-8")

    metadata = _markdown_metadata(path, path.name)

    assert metadata["title"] == "ICH Review"
    assert metadata["authors"] == ["Li Ming", "Wang Lei"]
    assert metadata["publication_year"] == 2024

