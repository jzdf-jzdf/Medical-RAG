"""Document-entry APIs, attachment lifecycle, and PDF-to-Markdown conversion."""

import asyncio
import re
import uuid
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pypdf import PdfReader
from pypdf.errors import DependencyError, PyPdfError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models import Document, ProcessingTask
from app.repositories.documents import DocumentRepository
from app.repositories.processing_tasks import ProcessingTaskRepository
from app.schemas.dashboard import ProcessingTaskRead
from app.schemas.documents import (
    AttachmentMove,
    DocumentCreate,
    DocumentBulkDeletionResult,
    DocumentDeletionSummary,
    DocumentList,
    DocumentRead,
    DocumentUpdate,
)

router = APIRouter(prefix="/documents", tags=["documents"])
settings = get_settings()

ALLOWED_FILE_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown; charset=utf-8",
}
DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")


@dataclass(frozen=True)
class StoredUpload:
    original_name: str
    storage_path: str
    digest: str
    media_type: str
    size_bytes: int
    created_new: bool


def _title_from_filename(filename: str) -> str:
    title = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return " ".join(title.split()) or "未命名文献"


def _entry_title_from_filename(filename: str) -> str:
    """Use the uploaded filename as the stable default identity of a new entry."""

    return Path(filename).stem.strip() or "未命名文献"


def _safe_storage_path(storage_path: str) -> Path:
    root = settings.workspace_root.resolve()
    path = (root / storage_path).resolve()
    if not path.is_relative_to(root):
        raise RuntimeError("Invalid attachment storage path")
    return path


def _file_kind(filename: str) -> str:
    return "markdown" if Path(filename).suffix.lower() == ".md" else "pdf"


async def _store_file(file: UploadFile) -> StoredUpload:
    """Stream an upload to content-addressed storage without loading it into memory."""

    original_name = Path(file.filename or "document").name
    suffix = Path(original_name).suffix.lower()
    media_type = ALLOWED_FILE_TYPES.get(suffix)
    if media_type is None:
        raise HTTPException(status_code=415, detail="当前仅支持 PDF 和 Markdown 文件")

    pending_dir = settings.workspace_root / ".uploads"
    pending_dir.mkdir(parents=True, exist_ok=True)
    pending_path = pending_dir / f"{uuid.uuid4()}.upload"
    digest = sha256()
    size_bytes = 0
    max_bytes = settings.max_upload_mb * 1024 * 1024

    try:
        with pending_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"单个文件不能超过 {settings.max_upload_mb} MB",
                    )
                digest.update(chunk)
                destination.write(chunk)
    except Exception:
        pending_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if size_bytes == 0:
        pending_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="不能上传空文件")

    digest_value = digest.hexdigest()
    relative_path = Path("attachments") / digest_value[:2] / f"{digest_value}{suffix}"
    final_path = settings.workspace_root / relative_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    created_new = not final_path.exists()
    if created_new:
        pending_path.replace(final_path)
    else:
        pending_path.unlink(missing_ok=True)

    return StoredUpload(
        original_name=original_name,
        storage_path=relative_path.as_posix(),
        digest=digest_value,
        media_type=media_type,
        size_bytes=size_bytes,
        created_new=created_new,
    )


def _remove_stored_upload(upload: StoredUpload) -> None:
    if upload.created_new:
        _safe_storage_path(upload.storage_path).unlink(missing_ok=True)


def _remove_storage_paths(paths: list[str]) -> None:
    for storage_path in paths:
        _safe_storage_path(storage_path).unlink(missing_ok=True)


def _split_authors(value: str | None) -> list[str]:
    if not value:
        return []
    return [name.strip() for name in re.split(r"\s*(?:;|，|\band\b)\s*", value) if name.strip()]


def _clean_doi(value: str) -> str:
    return value.rstrip(".,;:)]}>")


def _pdf_metadata(path: Path, filename: str) -> dict[str, Any]:
    fallback = {
        "title": _title_from_filename(filename),
        "metadata": {"metadata_source": "filename", "metadata_extracted": False},
    }
    try:
        reader = PdfReader(path)
        raw = reader.metadata or {}
        sample = "\n".join((page.extract_text() or "") for page in reader.pages[:3])
    except (OSError, TypeError, ValueError, DependencyError, PyPdfError):
        # Metadata recognition is best-effort. Unsupported encryption, optional
        # PDF codecs, or malformed page content must not invalidate the upload.
        return fallback

    raw_title = str(raw.get("/Title") or "").strip()
    title = raw_title if raw_title and raw_title.lower() not in {"untitled", "unknown"} else ""
    if not title:
        candidates = [
            line.strip() for line in sample.splitlines()[:25] if 6 <= len(line.strip()) <= 300
        ]
        title = next(
            (line for line in candidates if not DOI_PATTERN.search(line) and not line.isdigit()),
            _title_from_filename(filename),
        )

    doi_match = DOI_PATTERN.search(sample)
    year_source = " ".join(
        str(raw.get(key) or "") for key in ("/CreationDate", "/ModDate", "/Subject")
    )
    year_match = YEAR_PATTERN.search(year_source) or YEAR_PATTERN.search(sample[:5000])
    subject = str(raw.get("/Subject") or "").strip()
    authors = _split_authors(str(raw.get("/Author") or "").strip())

    return {
        "title": title,
        "doi": _clean_doi(doi_match.group(0)) if doi_match else None,
        "authors": authors,
        "abstract": subject or None,
        "publication_year": int(year_match.group(0)) if year_match else None,
        "metadata": {
            "item_type": "journalArticle",
            "metadata_source": "pdf",
            "metadata_extracted": True,
        },
    }


def _markdown_metadata(path: Path, filename: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")

    frontmatter: dict[str, str] = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    frontmatter[key.strip().lower()] = value.strip().strip("\"'")
    heading = next(
        (line.lstrip("#").strip() for line in text.splitlines() if line.startswith("# ")),
        "",
    )
    doi_match = DOI_PATTERN.search(frontmatter.get("doi", "") or text[:5000])
    year_match = YEAR_PATTERN.search(frontmatter.get("year", "") or frontmatter.get("date", ""))
    return {
        "title": frontmatter.get("title") or heading or _title_from_filename(filename),
        "doi": _clean_doi(doi_match.group(0)) if doi_match else None,
        "authors": _split_authors(frontmatter.get("authors") or frontmatter.get("author")),
        "abstract": frontmatter.get("abstract") or None,
        "publication_year": int(year_match.group(0)) if year_match else None,
        "metadata": {
            "item_type": frontmatter.get("type", "document"),
            "metadata_source": "markdown",
            "metadata_extracted": True,
        },
    }


async def _extract_metadata(upload: StoredUpload) -> dict[str, Any]:
    path = _safe_storage_path(upload.storage_path)
    if Path(upload.original_name).suffix.lower() == ".pdf":
        return await asyncio.to_thread(_pdf_metadata, path, upload.original_name)
    return await asyncio.to_thread(_markdown_metadata, path, upload.original_name)


def _file_values(upload: StoredUpload) -> dict[str, object]:
    return {
        "original_name": upload.original_name,
        "kind": _file_kind(upload.original_name),
        "source": "upload",
        "storage_path": upload.storage_path,
        "sha256": upload.digest,
        "media_type": upload.media_type,
        "size_bytes": upload.size_bytes,
    }


def _document_payload(upload: StoredUpload, extracted: dict[str, Any]) -> DocumentCreate:
    metadata = dict(extracted.get("metadata") or {})
    metadata["import_mode"] = "file"
    return DocumentCreate(
        title=_entry_title_from_filename(upload.original_name),
        doi=extracted.get("doi"),
        authors=extracted.get("authors") or [],
        abstract=extracted.get("abstract"),
        publication_year=extracted.get("publication_year"),
        metadata=metadata,
    )


async def _store_batch(files: list[UploadFile]) -> list[StoredUpload]:
    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一个文件")
    uploads: list[StoredUpload] = []
    try:
        for file in files:
            uploads.append(await _store_file(file))
    except Exception:
        for upload in uploads:
            _remove_stored_upload(upload)
        raise
    if len({upload.digest for upload in uploads}) != len(uploads):
        for upload in uploads:
            _remove_stored_upload(upload)
        raise HTTPException(status_code=409, detail="所选文件中包含重复内容")
    return uploads


async def _reject_existing_files(
    repository: DocumentRepository, uploads: list[StoredUpload]
) -> None:
    if await repository.any_file_exists([upload.digest for upload in uploads]):
        for upload in uploads:
            _remove_stored_upload(upload)
        raise HTTPException(status_code=409, detail="一个或多个文件已存在于文献库中")


async def _reject_active_processing_task(
    db: AsyncSession, document_id: uuid.UUID
) -> None:
    """Keep a Worker's input and output slots stable for its entire lifetime.

    The Worker resolves the PDF when it starts and later replaces the Markdown
    slot. Deleting, moving, or replacing either attachment during that window
    could make the result target a different document state.
    """

    task = await ProcessingTaskRepository(db).active_for_resource(document_id)
    if task:
        raise HTTPException(
            status_code=409,
            detail="该文献正在执行后台处理，请等待任务完成后再修改或删除附件",
        )


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def create_document(
    payload: DocumentCreate, db: AsyncSession = Depends(get_db)
) -> DocumentRead:
    return await DocumentRepository(db).create(payload)


@router.get("", response_model=DocumentList)
async def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> DocumentList:
    items, total = await DocumentRepository(db).list(page, page_size, q)
    return DocumentList(items=items, total=total, page=page, page_size=page_size)


@router.get("/deletion-summary", response_model=DocumentDeletionSummary)
async def document_deletion_summary(
    db: AsyncSession = Depends(get_db),
) -> DocumentDeletionSummary:
    return DocumentDeletionSummary(**await DocumentRepository(db).deletion_summary())


@router.post("/delete-unprotected", response_model=DocumentBulkDeletionResult)
async def delete_unprotected_documents(
    db: AsyncSession = Depends(get_db),
) -> DocumentBulkDeletionResult:
    storage_paths, result = await DocumentRepository(db).delete_unprotected()
    _remove_storage_paths(storage_paths)
    return DocumentBulkDeletionResult(**result)


@router.post("/from-file", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def create_document_from_file(
    file: UploadFile = File(...), db: AsyncSession = Depends(get_db)
) -> DocumentRead:
    documents = await create_documents_from_files(files=[file], db=db)
    return documents[0]


@router.post("/from-files", response_model=list[DocumentRead], status_code=status.HTTP_201_CREATED)
async def create_documents_from_files(
    files: list[UploadFile] = File(...), db: AsyncSession = Depends(get_db)
) -> list[DocumentRead]:
    uploads = await _store_batch(files)
    repository = DocumentRepository(db)
    await _reject_existing_files(repository, uploads)
    try:
        extracted = [await _extract_metadata(upload) for upload in uploads]
    except Exception:
        # If a future extractor fails unexpectedly, do not leave orphan files in
        # content-addressed storage before any database row has been committed.
        for upload in uploads:
            _remove_stored_upload(upload)
        raise
    items = [
        (_document_payload(upload, metadata), _file_values(upload))
        for upload, metadata in zip(uploads, extracted, strict=True)
    ]
    try:
        documents = await repository.create_many_with_files(items)
        for index, (document, upload) in enumerate(zip(documents, uploads, strict=True)):
            if _file_kind(upload.original_name) == "markdown":
                documents[index] = await repository.set_markdown_version(
                    document,
                    storage_path=upload.storage_path,
                    parser="upload",
                    parser_version="1",
                )
        return documents
    except IntegrityError as exc:
        await db.rollback()
        for upload in uploads:
            _remove_stored_upload(upload)
        raise HTTPException(status_code=409, detail="文件或识别出的 DOI 已存在") from exc


@router.post(
    "/{document_id}/files", response_model=DocumentRead, status_code=status.HTTP_201_CREATED
)
async def add_document_file(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> DocumentRead:
    return await add_document_files(document_id=document_id, files=[file], db=db)


@router.post(
    "/{document_id}/files/batch",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_document_files(
    document_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    replace_existing: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> DocumentRead:
    """Attach at most one file of each kind, optionally replacing existing versions."""

    repository = DocumentRepository(db)
    document = await repository.get(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文献条目不存在")
    await _reject_active_processing_task(db, document_id)

    uploads = await _store_batch(files)
    kinds = [_file_kind(upload.original_name) for upload in uploads]
    if len(set(kinds)) != len(kinds):
        for upload in uploads:
            _remove_stored_upload(upload)
        raise HTTPException(status_code=409, detail="同一条目一次最多上传一个 PDF 和一个 MD")
    await _reject_existing_files(repository, uploads)
    try:
        document, removed_paths = await repository.add_files(
            document,
            [_file_values(item) for item in uploads],
            replace_existing=replace_existing,
        )
        _remove_storage_paths(removed_paths)
        extracted_items = [await _extract_metadata(upload) for upload in uploads]
        merged: dict[str, Any] = {"metadata": {}}
        for extracted in extracted_items:
            for field in ("title", "doi", "authors", "abstract", "publication_year"):
                if not merged.get(field) and extracted.get(field):
                    merged[field] = extracted[field]
            merged["metadata"].update(extracted.get("metadata") or {})
        document = await repository.fill_missing_metadata(document, merged)
        markdown = next(
            (upload for upload in uploads if _file_kind(upload.original_name) == "markdown"), None
        )
        if markdown:
            document = await repository.set_markdown_version(
                document,
                storage_path=markdown.storage_path,
                parser="upload",
                parser_version="1",
            )
        return document
    except FileExistsError as exc:
        await db.rollback()
        for upload in uploads:
            _remove_stored_upload(upload)
        raise HTTPException(
            status_code=409,
            detail=f"{exc}；确认后可替代原文件",
        ) from exc
    except IntegrityError as exc:
        await db.rollback()
        for upload in uploads:
            _remove_stored_upload(upload)
        raise HTTPException(status_code=409, detail="文件或识别出的 DOI 已存在") from exc


@router.post(
    "/{document_id}/convert-pdf",
    response_model=ProcessingTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def convert_document_pdf(
    document_id: uuid.UUID,
    replace_existing: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> ProcessingTask:
    """Validate conversion preconditions, enqueue durable work, and return 202.

    The request does not invoke MinerU. It records all user intent required by
    the Worker, allowing processing to continue if the browser disconnects.
    Duplicate concurrent requests resolve to the existing active queue row.
    """

    repository = DocumentRepository(db)
    document = await repository.get(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文献条目不存在")
    pdf_file = next((file for file in document.files if file.kind == "pdf"), None)
    if pdf_file is None:
        raise HTTPException(status_code=409, detail="该条目没有可转换的 PDF 文件")
    if any(file.kind == "markdown" for file in document.files) and not replace_existing:
        raise HTTPException(
            status_code=409,
            detail="该条目已经有 MD 文件，MinerU 转换结果会替代现有 MD，请确认后继续",
        )

    # Fail before enqueue when the database row points to a missing physical
    # input; otherwise the UI would briefly show an avoidable queued task.
    pdf_path = _safe_storage_path(pdf_file.storage_path)
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="PDF 文件已丢失")
    task, _created = await ProcessingTaskRepository(db).enqueue_pdf_conversion(
        document_id=document.id,
        document_name=document.title,
        replace_existing=replace_existing,
    )
    return task


@router.get("/attachments/{file_id}/content", response_class=FileResponse)
async def attachment_content(
    file_id: uuid.UUID,
    download: bool = False,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    attachment = await DocumentRepository(db).get_file(file_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="附件不存在")
    path = _safe_storage_path(attachment.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="附件文件已丢失")
    return FileResponse(
        path,
        media_type=attachment.media_type,
        filename=attachment.original_name,
        content_disposition_type="attachment" if download else "inline",
    )


@router.delete("/attachments/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(file_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    repository = DocumentRepository(db)
    attachment = await repository.get_file(file_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="附件不存在")
    await _reject_active_processing_task(db, attachment.document_id)
    storage_path = attachment.storage_path
    await repository.delete_file(attachment)
    _remove_storage_paths([storage_path])


@router.post("/attachments/{file_id}/move", response_model=DocumentRead)
async def move_attachment(
    file_id: uuid.UUID,
    payload: AttachmentMove,
    db: AsyncSession = Depends(get_db),
) -> DocumentRead:
    repository = DocumentRepository(db)
    attachment = await repository.get_file(file_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="附件不存在")
    await _reject_active_processing_task(db, attachment.document_id)
    target = await repository.get(payload.target_document_id)
    if not target:
        raise HTTPException(status_code=404, detail="目标条目不存在")
    await _reject_active_processing_task(db, target.id)
    try:
        return await repository.move_file(attachment, target)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(document_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> DocumentRead:
    document = await DocumentRepository(db).get(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文献条目不存在")
    return document


@router.patch("/{document_id}", response_model=DocumentRead)
async def update_document(
    document_id: uuid.UUID, payload: DocumentUpdate, db: AsyncSession = Depends(get_db)
) -> DocumentRead:
    repository = DocumentRepository(db)
    document = await repository.get(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文献条目不存在")
    return await repository.update(document, payload)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    repository = DocumentRepository(db)
    document: Document | None = await repository.get_for_update(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文献条目不存在")
    await _reject_active_processing_task(db, document_id)
    knowledge_base_names = await repository.knowledge_base_names(document_id)
    if knowledge_base_names:
        names = "、".join(knowledge_base_names[:3])
        suffix = "等" if len(knowledge_base_names) > 3 else ""
        raise HTTPException(
            status_code=409,
            detail=f"该文献已加入知识库“{names}”{suffix}，请先从知识库中移除后再删除",
        )
    storage_paths = [file.storage_path for file in document.files]
    await repository.delete(document)
    _remove_storage_paths(storage_paths)
