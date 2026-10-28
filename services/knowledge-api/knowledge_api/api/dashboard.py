"""Aggregate persisted project data for the welcome page and activity view."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from knowledge_api.db.session import get_db
from knowledge_api.models import Chunk, Collection, Document, DocumentFile
from knowledge_api.schemas.dashboard import DashboardRead, RecentDocumentRead

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardRead)
async def get_dashboard(db: AsyncSession = Depends(get_db)) -> DashboardRead:
    """Return only persisted counts and recent documents; no sample data is synthesized."""

    week_start = datetime.now(UTC) - timedelta(days=7)
    documents_total = int(
        (await db.scalar(select(func.count()).select_from(Document))) or 0
    )
    documents_this_week = int(
        (
            await db.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.created_at >= week_start)
            )
        )
        or 0
    )
    markdown_ready = int(
        (
            await db.scalar(
                select(func.count(func.distinct(DocumentFile.document_id))).where(
                    DocumentFile.kind == "markdown"
                )
            )
        )
        or 0
    )
    knowledge_bases = int(
        (await db.scalar(select(func.count()).select_from(Collection))) or 0
    )
    persisted_chunks = int(
        (await db.scalar(select(func.count()).select_from(Chunk))) or 0
    )

    recent = list(
        (
            await db.scalars(
                select(Document)
                .options(selectinload(Document.files))
                .order_by(Document.created_at.desc())
                .limit(5)
            )
        ).all()
    )
    document_ids = [document.id for document in recent]
    chunk_counts: dict = {}
    if document_ids:
        rows = (
            await db.execute(
                select(Chunk.document_id, func.count(Chunk.id))
                .where(Chunk.document_id.in_(document_ids))
                .group_by(Chunk.document_id)
            )
        ).all()
        chunk_counts = {document_id: int(count) for document_id, count in rows}

    return DashboardRead(
        documents_total=documents_total,
        documents_this_week=documents_this_week,
        markdown_ready=markdown_ready,
        knowledge_bases=knowledge_bases,
        persisted_chunks=persisted_chunks,
        recent_documents=[
            RecentDocumentRead(
                id=document.id,
                title=document.title,
                item_type=str((document.metadata_json or {}).get("item_type", "document")),
                attachment_count=len(document.files),
                chunk_count=chunk_counts.get(document.id, 0),
                has_pdf=any(file.kind == "pdf" for file in document.files),
                has_markdown=any(file.kind == "markdown" for file in document.files),
                created_at=document.created_at,
            )
            for document in recent
        ],
    )
