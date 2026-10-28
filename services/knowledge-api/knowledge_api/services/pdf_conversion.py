"""Persist one MinerU conversion result as the document's active Markdown version."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_api.core.config import get_settings
from knowledge_api.repositories.documents import DocumentRepository
from knowledge_api.services.mineru import ProgressCallback, convert_pdf_to_markdown

settings = get_settings()
PhaseCallback = Callable[[], Awaitable[None]]


class PdfConversionError(RuntimeError):
    """Raised when a queued document can no longer be converted safely."""


@dataclass(frozen=True)
class PdfConversionResult:
    """Stable identifiers returned after both file and database persistence."""

    document_id: uuid.UUID
    attachment_id: uuid.UUID
    storage_path: str


def _safe_storage_path(storage_path: str) -> Path:
    """Resolve an attachment path while preventing workspace traversal."""

    root = settings.workspace_root.resolve()
    path = (root / storage_path).resolve()
    if not path.is_relative_to(root):
        raise PdfConversionError("附件存储路径无效")
    return path


async def convert_document_pdf(
    db: AsyncSession,
    document_id: uuid.UUID,
    *,
    replace_existing: bool,
    on_output: ProgressCallback | None = None,
    on_persisting: PhaseCallback | None = None,
) -> PdfConversionResult:
    """Convert a document PDF and install its Markdown attachment atomically.

    MinerU execution happens before database mutation. The generated content is
    content-addressed by SHA-256, then the repository updates the attachment and
    Markdown-version records. A newly written output is removed when persistence
    reports an error so an unreferenced partial result is not left on disk.
    """

    repository = DocumentRepository(db)
    document = await repository.get(document_id)
    if document is None:
        raise PdfConversionError("文献条目不存在")
    # Re-read attachment slots at execution time: queue wait may outlive the API
    # request that created the task, and stale input must fail explicitly.
    pdf_file = next((file for file in document.files if file.kind == "pdf"), None)
    if pdf_file is None:
        raise PdfConversionError("该条目没有可转换的 PDF 文件")
    existing_markdown = next(
        (file for file in document.files if file.kind == "markdown"), None
    )
    if existing_markdown and not replace_existing:
        raise PdfConversionError("该条目已有 MD 文件，需要确认替代后才能转换")

    pdf_path = _safe_storage_path(pdf_file.storage_path)
    if not pdf_path.is_file():
        raise PdfConversionError("PDF 文件已丢失")
    # No database transaction is held during the expensive external process.
    markdown_content = await convert_pdf_to_markdown(pdf_path, on_output=on_output)
    if on_persisting:
        await on_persisting()

    # Content-addressed storage makes identical parser output deterministic and
    # enables exact duplicate detection without trusting file names.
    digest = sha256(markdown_content).hexdigest()
    relative_path = Path("attachments") / digest[:2] / f"{digest}.md"
    output_path = settings.workspace_root / relative_path
    created_new = not output_path.exists()

    # Reusing the same content for this document is idempotent. Sharing one
    # physical attachment between different documents remains forbidden by the
    # current ownership model, so that deletion is always unambiguous.
    if await repository.file_exists(digest):
        if existing_markdown and existing_markdown.sha256 == digest:
            return PdfConversionResult(
                document_id=document.id,
                attachment_id=existing_markdown.id,
                storage_path=existing_markdown.storage_path,
            )
        raise PdfConversionError("MinerU 生成的 MD 已存在于其他文献条目中")

    # The file must exist before the database points at it. Conversely, if the
    # database commit fails below, only a file created by this call is removed.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if created_new:
        output_path.write_bytes(markdown_content)
    file_values: dict[str, object] = {
        "original_name": f"{Path(pdf_file.original_name).stem}.md",
        "kind": "markdown",
        "source": "mineru",
        "storage_path": relative_path.as_posix(),
        "sha256": digest,
        "media_type": "text/markdown; charset=utf-8",
        "size_bytes": len(markdown_content),
    }
    try:
        # add_files commits the attachment slot first; set_markdown_version then
        # records parser provenance. Repository rules enforce one PDF and one MD.
        document, removed_paths = await repository.add_files(
            document,
            [file_values],
            replace_existing=replace_existing,
        )
        document = await repository.set_markdown_version(
            document,
            storage_path=relative_path.as_posix(),
            parser="mineru",
            parser_version="4",
        )
        # Old physical Markdown is removed only after the replacement commits.
        for storage_path in removed_paths:
            _safe_storage_path(storage_path).unlink(missing_ok=True)
    except (FileExistsError, IntegrityError) as exc:
        await db.rollback()
        if created_new:
            output_path.unlink(missing_ok=True)
        raise PdfConversionError(str(exc)) from exc

    attachment = next(
        (file for file in document.files if file.storage_path == relative_path.as_posix()),
        None,
    )
    if attachment is None:
        raise PdfConversionError("Markdown 已保存，但附件记录读取失败")
    return PdfConversionResult(
        document_id=document.id,
        attachment_id=attachment.id,
        storage_path=attachment.storage_path,
    )
