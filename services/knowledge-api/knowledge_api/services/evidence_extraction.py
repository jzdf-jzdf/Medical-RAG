"""Deterministic first-pass evidence candidate extraction for human review."""

import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_api.models import (
    Chunk,
    DocumentVersion,
    EvidenceSpan,
    ExtractionRun,
    Study,
)

ProgressCallback = Callable[[str, int, str], Awaitable[None]]
SENTENCE_PATTERN = re.compile(r"[^。！？\n]+[。！？]?|[^.!?\n]+[.!?]?")
EVIDENCE_MARKERS = (
    "推荐",
    "建议",
    "应当",
    "应该",
    "应",
    "不推荐",
    "显著",
    "风险",
    "死亡率",
    "有效",
    "randomized",
    "recommend",
    "significant",
    "mortality",
    "risk",
)


def evidence_sentences(content: str) -> list[tuple[int, int, str]]:
    """Return bounded, keyword-bearing sentences with exact local offsets."""

    candidates: list[tuple[int, int, str]] = []
    lowered = content.lower()
    for match in SENTENCE_PATTERN.finditer(content):
        quote = match.group(0).strip()
        if len(quote) < 12 or len(quote) > 1200:
            continue
        if quote.startswith("#") or ("推荐意见" in quote and "定义" in quote):
            continue
        start = match.start() + len(match.group(0)) - len(match.group(0).lstrip())
        end = start + len(quote)
        sentence_lowered = lowered[start:end]
        if any(marker in sentence_lowered for marker in EVIDENCE_MARKERS):
            candidates.append((start, end, quote))
        if len(candidates) >= 3:
            break
    return candidates


async def extract_collection_evidence(
    db: AsyncSession,
    collection_id: uuid.UUID,
    on_progress: ProgressCallback | None = None,
) -> dict[str, object]:
    chunks = list(
        (
            await db.scalars(
                select(Chunk)
                .where(Chunk.collection_id == collection_id)
                .order_by(Chunk.document_id, Chunk.chunk_index)
            )
        ).all()
    )
    if not chunks:
        raise ValueError("知识库没有可抽取的分块，请先构建向量索引")
    chunk_ids = [chunk.id for chunk in chunks]
    await db.execute(delete(EvidenceSpan).where(EvidenceSpan.chunk_id.in_(chunk_ids)))

    document_ids = list(dict.fromkeys(chunk.document_id for chunk in chunks if chunk.document_id))
    versions = list(
        (
            await db.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id.in_(document_ids))
                .order_by(DocumentVersion.document_id, DocumentVersion.version.desc())
            )
        ).all()
    )
    latest_versions: dict[uuid.UUID, DocumentVersion] = {}
    for version in versions:
        latest_versions.setdefault(version.document_id, version)

    studies: dict[uuid.UUID, Study] = {}
    runs: list[ExtractionRun] = []
    for document_id, version in latest_versions.items():
        study = Study(
            document_version_id=version.id,
            disease_id=None,
            study_type="evidence-candidate-set",
            schema_version="evidence-span-v1",
            metadata_json={"collection_id": str(collection_id)},
        )
        run = ExtractionRun(
            document_version_id=version.id,
            schema_version="evidence-span-v1",
            model_name="deterministic-medical-patterns-v1",
            status="running",
            raw_output=None,
        )
        db.add_all([study, run])
        studies[document_id] = study
        runs.append(run)
    await db.flush()

    count = 0
    for position, chunk in enumerate(chunks, start=1):
        study = studies.get(chunk.document_id)
        for local_start, local_end, quote in evidence_sentences(chunk.content):
            db.add(
                EvidenceSpan(
                    chunk_id=chunk.id,
                    study_id=study.id if study else None,
                    quote=quote,
                    page=chunk.page_start,
                    char_start=chunk.char_start + local_start,
                    char_end=chunk.char_start + local_end,
                    review_status="pending",
                )
            )
            count += 1
        if on_progress and (position == len(chunks) or position % 20 == 0):
            progress = 10 + round(position / len(chunks) * 80)
            await on_progress(
                "extracting", progress, f"已检查 {position}/{len(chunks)} 个知识块"
            )
    finished_at = datetime.now(UTC)
    for run in runs:
        run.status = "completed"
        run.finished_at = finished_at
        run.raw_output = {"collection_id": str(collection_id), "candidate_count": count}
    await db.commit()
    return {
        "collection_id": str(collection_id),
        "chunk_count": len(chunks),
        "evidence_candidate_count": count,
        "status": "completed",
    }
