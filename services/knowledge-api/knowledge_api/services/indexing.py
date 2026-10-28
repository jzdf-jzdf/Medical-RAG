"""Atomic knowledge-base chunking and embedding index construction."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from knowledge_api.core.config import get_settings
from knowledge_api.models import Chunk, Collection, Document, IndexVersion
from knowledge_api.services.chunking import validate_chunking_config
from knowledge_api.services.embeddings import download_medcpt_model, embed_texts
from knowledge_api.services.guideline_chunking import build_chunks_async
from knowledge_api.services.qdrant_store import (
    collection_name as qdrant_collection_name,
)
from knowledge_api.services.qdrant_store import (
    create_collection as create_qdrant_collection,
)
from knowledge_api.services.qdrant_store import (
    upsert_points,
)

ProgressCallback = Callable[[str, int, str], Awaitable[None]]


async def _document_text(document, *, strict: bool = False) -> str:
    from knowledge_api.api.knowledge_bases import _document_text as read_document_text

    return await read_document_text(document, strict=strict)


async def build_collection_index(
    db: AsyncSession,
    collection_id: uuid.UUID,
    on_progress: ProgressCallback | None = None,
) -> dict[str, object]:
    """Replace one collection's chunks and vectors in a single transaction."""

    collection = await db.scalar(
        select(Collection)
        .options(
            selectinload(Collection.documents).selectinload(Document.files),
            selectinload(Collection.chunking_method),
            selectinload(Collection.embedding_model),
        )
        .where(Collection.id == collection_id)
    )
    if collection is None:
        raise ValueError("知识库不存在")
    if collection.chunking_method is None or collection.embedding_model is None:
        raise ValueError("知识库缺少分块方法或嵌入模型")
    if not collection.documents:
        raise ValueError("知识库没有文献")

    method = collection.chunking_method
    model = collection.embedding_model
    if model.provider.lower().strip() == "medcpt":

        async def model_progress(phase: str, progress: int, message: str) -> None:
            if on_progress:
                await on_progress(phase, max(5, round(progress * 0.3)), message)

        await download_medcpt_model(model, on_progress=model_progress)
    normalized_config = validate_chunking_config(method.strategy, method.config or {}).model_dump()
    version = f"collection:{collection.id}:{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
    collection.status = "building"
    index = IndexVersion(
        collection_id=collection.id,
        version=version,
        status="building",
        embedding_model=model.model_name,
        reranker_model=get_settings().reranker_model or None,
        config={"provider": model.provider, "dimension": model.dimension},
        chunk_count=0,
    )
    db.add(index)
    await db.flush()
    qdrant_name = qdrant_collection_name(index.id)
    index.config = {**index.config, "qdrant_collection": qdrant_name}

    chunk_rows: list[Chunk] = []
    texts: list[str] = []
    total_documents = len(collection.documents)
    for document_number, document in enumerate(collection.documents, start=1):
        markdown_file = next((file for file in document.files if file.kind == "markdown"), None)
        if markdown_file is None:
            raise ValueError(f"文献“{document.title}”缺少 Markdown 文件")

        async def chunk_progress(phase, progress, message, document_number=document_number):
            if on_progress:
                fraction = 0 if progress is None else progress / 100
                overall = 30 + round(((document_number - 1) + fraction) / total_documents * 25)
                await on_progress(
                    phase, overall, f"文献 {document_number}/{total_documents}：{message}"
                )

        _, chunks = await build_chunks_async(
            await _document_text(document, strict=method.strategy == "guideline_semantic"),
            method.strategy,
            normalized_config,
            document_id=document.id,
            document_title=document.title,
            on_progress=chunk_progress,
        )
        if not chunks:
            raise ValueError(f"文献“{document.title}”的 Markdown 内容为空")
        for item in chunks:
            row = Chunk(
                collection_id=collection.id,
                document_id=document.id,
                document_file_id=markdown_file.id,
                chunk_index=item.index,
                index_version=version,
                content=item.content,
                embedding_text=item.embedding_text,
                heading_path=item.heading_path,
                char_start=item.char_start,
                char_end=item.char_end,
                metadata_json={
                    **item.metadata,
                    "chunking_method_id": str(method.id),
                    "strategy": method.strategy,
                    "config": normalized_config,
                },
            )
            chunk_rows.append(row)
            texts.append(item.embedding_text)
        if method.strategy == "guideline_semantic" and chunks:
            index.config = {
                **index.config,
                "guideline_runs": {
                    **index.config.get("guideline_runs", {}),
                    str(document.id): chunks[0].metadata["run_id"],
                },
            }
        if on_progress:
            progress = 30 + round(document_number / total_documents * 25)
            await on_progress(
                "chunking", progress, f"已完成 {document_number}/{total_documents} 篇文献分块"
            )

    db.add_all(chunk_rows)
    # SQLAlchemy UUID defaults are assigned during flush. Qdrant point IDs
    # must therefore be materialized before the external collection is written.
    await db.flush()
    batch_size = 64
    for start in range(0, len(texts), batch_size):
        vectors = await embed_texts(model, texts[start : start + batch_size])
        for row, vector in zip(chunk_rows[start : start + batch_size], vectors, strict=True):
            row.embedding = vector
        if on_progress:
            completed = min(start + batch_size, len(texts))
            progress = 55 + round(completed / len(texts) * 40)
            await on_progress("embedding", progress, f"已生成 {completed}/{len(texts)} 个向量")

    if on_progress:
        await on_progress("publishing", 96, "正在发布 Qdrant 蓝绿索引")
    dimension = len(chunk_rows[0].embedding or []) if chunk_rows else 0
    if dimension <= 0:
        raise ValueError("嵌入模型未返回有效向量")
    await create_qdrant_collection(qdrant_name, dimension)
    await upsert_points(
        qdrant_name,
        (
            {
                "id": str(row.id),
                "vector": row.embedding,
                "payload": {
                    "chunk_id": str(row.id),
                    "document_id": str(row.document_id),
                    "collection_id": str(collection.id),
                    "index_version": version,
                    "atomic_chunk_id": (row.metadata_json or {}).get("chunk_id"),
                    "chunk_type": (row.metadata_json or {}).get("type"),
                    "topic_tags": (row.metadata_json or {}).get("topic_tags", []),
                },
            }
            for row in chunk_rows
        ),
    )
    # Only cut over after the green PostgreSQL snapshot and Qdrant collection
    # are complete. Readers keep using the previous active version until commit.
    await db.execute(
        update(IndexVersion)
        .where(
            IndexVersion.collection_id == collection.id,
            IndexVersion.status == "active",
            IndexVersion.id != index.id,
        )
        .values(status="superseded")
    )
    index.status = "active"
    index.chunk_count = len(chunk_rows)
    index.activated_at = datetime.now(UTC)
    collection.status = "indexed"
    await db.commit()
    return {
        "collection_id": str(collection.id),
        "index_version_id": str(index.id),
        "version": version,
        "chunk_count": len(chunk_rows),
        "status": "indexed",
    }


async def mark_collection_failed(db: AsyncSession, collection_id: uuid.UUID, message: str) -> None:
    active = await db.scalar(
        select(IndexVersion.id).where(
            IndexVersion.collection_id == collection_id,
            IndexVersion.status == "active",
        )
    )
    await db.execute(
        update(Collection)
        .where(Collection.id == collection_id)
        .values(status="indexed" if active else "failed")
    )
    await db.execute(
        update(IndexVersion)
        .where(IndexVersion.collection_id == collection_id, IndexVersion.status == "building")
        .values(status="failed", error_message=message[:4000])
    )
    await db.commit()
