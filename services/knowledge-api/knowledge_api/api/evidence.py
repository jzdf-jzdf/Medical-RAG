"""Evidence extraction queueing, review inbox, and audit decisions."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_api.core.security import principal_from_request
from knowledge_api.db.session import get_db
from knowledge_api.models import Chunk, Collection, Document, EvidenceSpan, ProcessingTask, ReviewDecision
from knowledge_api.repositories.processing_tasks import ProcessingTaskRepository
from knowledge_api.schemas.dashboard import ProcessingTaskRead
from knowledge_api.schemas.evidence import EvidenceReviewRead, EvidenceReviewRequest, EvidenceSpanRead

router = APIRouter(tags=["evidence"])


@router.post(
    "/knowledge-bases/{collection_id}/extract-evidence",
    response_model=ProcessingTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def extract_evidence(
    collection_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ProcessingTask:
    collection = await db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if collection.status != "indexed":
        raise HTTPException(status_code=409, detail="请先完成知识库向量构建")
    task, _ = await ProcessingTaskRepository(db).enqueue(
        task_type="evidence_extraction",
        resource_type="knowledge_base",
        resource_id=collection.id,
        resource_name=collection.name,
    )
    return task


@router.get("/evidence-spans", response_model=list[EvidenceSpanRead])
async def list_evidence_spans(
    collection_id: uuid.UUID | None = None,
    review_status: str | None = Query(default=None, pattern="^(pending|approved|rejected)$"),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[EvidenceSpanRead]:
    statement = (
        select(EvidenceSpan, Chunk, Document)
        .join(Chunk, Chunk.id == EvidenceSpan.chunk_id)
        .join(Document, Document.id == Chunk.document_id)
        .order_by(EvidenceSpan.id)
        .limit(limit)
    )
    if collection_id:
        statement = statement.where(Chunk.collection_id == collection_id)
    if review_status:
        statement = statement.where(EvidenceSpan.review_status == review_status)
    rows = (await db.execute(statement)).all()
    return [
        EvidenceSpanRead(
            id=span.id,
            chunk_id=span.chunk_id,
            document_id=document.id,
            document_title=document.title,
            quote=span.quote,
            heading_path=chunk.heading_path or [],
            page=span.page,
            char_start=span.char_start,
            char_end=span.char_end,
            review_status=span.review_status,
        )
        for span, chunk, document in rows
    ]


@router.post("/evidence-spans/{span_id}/review", response_model=EvidenceReviewRead)
async def review_evidence_span(
    span_id: uuid.UUID,
    payload: EvidenceReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> EvidenceReviewRead:
    span = await db.get(EvidenceSpan, span_id)
    if span is None:
        raise HTTPException(status_code=404, detail="证据候选不存在")
    principal = principal_from_request(request)
    reviewer_id = principal.user_id if principal else "anonymous"
    decision = ReviewDecision(
        evidence_span_id=span.id,
        reviewer_id=reviewer_id,
        decision=payload.decision,
        comment=payload.comment,
    )
    span.review_status = payload.decision
    db.add(decision)
    await db.commit()
    await db.refresh(decision)
    return EvidenceReviewRead(
        evidence_span_id=span.id,
        reviewer_id=decision.reviewer_id,
        decision=decision.decision,
        comment=decision.comment,
        created_at=decision.created_at,
    )
