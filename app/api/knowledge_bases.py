"""Registration, preview, and persisted construction of Markdown knowledge bases."""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.session import get_db
from app.models import (
    Chunk,
    ChunkingMethod,
    Collection,
    Document,
    EmbeddingModel,
    IndexVersion,
    ProcessingTask,
)
from app.repositories.processing_tasks import ProcessingTaskRepository
from app.schemas.dashboard import ProcessingTaskRead
from app.schemas.knowledge_bases import (
    ChunkingMethodCreate,
    ChunkingMethodRead,
    ChunkPreviewItem,
    ChunkPreviewRequest,
    ChunkPreviewStats,
    ChunkRecipePreview,
    DocumentChunkPreview,
    EmbeddingModelCreate,
    EmbeddingModelRead,
    IndexVersionList,
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.services.chunking import ChunkResult, build_chunks, validate_chunking_config
from app.services.retrieval import search_collection

router = APIRouter(tags=["knowledge bases"])
settings = get_settings()


def _normalized_name(value: str) -> str:
    return " ".join(value.split())


def _safe_attachment_path(storage_path: str) -> Path:
    root = settings.workspace_root.resolve()
    path = (root / storage_path).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(status_code=400, detail="附件路径无效")
    return path


async def _document_text(document: Document) -> str:
    """Load the only source format accepted by the knowledge-base pipeline: MD."""

    markdown_file = next((file for file in document.files if file.kind == "markdown"), None)
    if markdown_file is None:
        raise HTTPException(status_code=409, detail="该文献没有可用于知识库构建的 MD 版本")

    path = _safe_attachment_path(markdown_file.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="MD 文件不存在")
    return await asyncio.to_thread(path.read_text, encoding="utf-8-sig", errors="replace")


def build_chunk_preview(text: str, strategy: str, config: dict) -> list[ChunkPreviewItem]:
    _, chunks = build_chunks(text, strategy, config)
    return [_chunk_item(chunk) for chunk in chunks]


def _chunk_item(chunk: ChunkResult) -> ChunkPreviewItem:
    return ChunkPreviewItem(
        index=chunk.index,
        content=chunk.content,
        embedding_text=chunk.embedding_text,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        char_count=len(chunk.content),
        heading_path=chunk.heading_path,
    )


def _chunk_stats(chunks: list[ChunkPreviewItem], config: dict) -> ChunkPreviewStats:
    lengths = [chunk.char_count for chunk in chunks]
    minimum = int(config.get("min_chars", 0))
    maximum = int(config.get("max_chars", 1200))
    return ChunkPreviewStats(
        total=len(chunks),
        average_chars=round(sum(lengths) / len(lengths)) if lengths else 0,
        minimum_chars=min(lengths, default=0),
        maximum_chars=max(lengths, default=0),
        too_short=sum(length < minimum for length in lengths),
        too_long=sum(length > maximum for length in lengths),
    )


async def _get_knowledge_base(db: AsyncSession, collection_id: uuid.UUID) -> Collection | None:
    statement = (
        select(Collection)
        .options(
            selectinload(Collection.documents).selectinload(Document.files),
            selectinload(Collection.chunking_method),
            selectinload(Collection.embedding_model),
        )
        .where(Collection.id == collection_id)
    )
    return await db.scalar(statement)


@router.get("/configurations/chunking-methods", response_model=list[ChunkingMethodRead])
async def list_chunking_methods(db: AsyncSession = Depends(get_db)) -> list[ChunkingMethod]:
    return list(
        (await db.scalars(select(ChunkingMethod).order_by(ChunkingMethod.created_at))).all()
    )


@router.post(
    "/configurations/chunking-methods/preview",
    response_model=ChunkRecipePreview,
)
async def preview_chunking_recipe(
    payload: ChunkPreviewRequest,
    db: AsyncSession = Depends(get_db),
) -> ChunkRecipePreview:
    """Dry-run an unsaved recipe against a real document without writing chunks."""

    document = await db.scalar(
        select(Document)
        .options(selectinload(Document.files))
        .where(Document.id == payload.document_id)
    )
    if document is None:
        raise HTTPException(status_code=404, detail="样本文献不存在")
    try:
        config, chunks = build_chunks(
            await _document_text(document), payload.strategy, payload.config
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    items = [_chunk_item(chunk) for chunk in chunks]
    normalized = config.model_dump()
    return ChunkRecipePreview(
        document_id=document.id,
        document_title=document.title,
        strategy=payload.strategy,
        config=normalized,
        chunks=items,
        stats=_chunk_stats(items, normalized),
    )


@router.post(
    "/configurations/chunking-methods",
    response_model=ChunkingMethodRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_chunking_method(
    payload: ChunkingMethodCreate, db: AsyncSession = Depends(get_db)
) -> ChunkingMethod:
    values = payload.model_dump()
    try:
        values["config"] = validate_chunking_config(
            str(values["strategy"]), dict(values["config"])
        ).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    method = ChunkingMethod(**values)
    method.name = _normalized_name(method.name)
    db.add(method)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="分块方法名称已存在") from exc
    await db.refresh(method)
    return method


@router.delete(
    "/configurations/chunking-methods/{method_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_chunking_method(method_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    method = await db.get(ChunkingMethod, method_id)
    if method is None:
        raise HTTPException(status_code=404, detail="分块方法不存在")
    usage = await db.scalar(
        select(func.count())
        .select_from(Collection)
        .where(Collection.chunking_method_id == method_id)
    )
    if usage:
        raise HTTPException(status_code=409, detail="该分块方法已被知识库使用，不能删除")
    await db.delete(method)
    await db.commit()


@router.get("/configurations/embedding-models", response_model=list[EmbeddingModelRead])
async def list_embedding_models(db: AsyncSession = Depends(get_db)) -> list[EmbeddingModel]:
    return list(
        (await db.scalars(select(EmbeddingModel).order_by(EmbeddingModel.created_at))).all()
    )


@router.post(
    "/configurations/embedding-models",
    response_model=EmbeddingModelRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_embedding_model(
    payload: EmbeddingModelCreate, db: AsyncSession = Depends(get_db)
) -> EmbeddingModel:
    values = payload.model_dump()
    if str(values["provider"]).lower().strip() == "medcpt":
        config = dict(values.get("config") or {})
        config.setdefault("query_model", settings.medcpt_query_model)
        config.setdefault("article_model", settings.medcpt_article_model)
        values.update(
            provider="medcpt",
            model_name=config["article_model"],
            dimension=768,
            base_url=None,
            config=config,
        )
    model = EmbeddingModel(**values)
    model.name = _normalized_name(model.name)
    db.add(model)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="嵌入模型名称已存在") from exc
    await db.refresh(model)
    return model


@router.post(
    "/configurations/embedding-models/{model_id}/download",
    response_model=ProcessingTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def download_embedding_model(
    model_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ProcessingTask:
    model = await db.get(EmbeddingModel, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="嵌入模型不存在")
    if model.provider.lower().strip() != "medcpt":
        raise HTTPException(status_code=409, detail="当前仅 MedCPT 支持服务内模型下载")
    task, _ = await ProcessingTaskRepository(db).enqueue(
        task_type="embedding_model_download",
        resource_type="embedding_model",
        resource_id=model.id,
        resource_name=model.name,
    )
    return task


@router.delete(
    "/configurations/embedding-models/{model_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_embedding_model(model_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    model = await db.get(EmbeddingModel, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="嵌入模型不存在")
    usage = await db.scalar(
        select(func.count())
        .select_from(Collection)
        .where(Collection.embedding_model_id == model_id)
    )
    if usage:
        raise HTTPException(status_code=409, detail="该嵌入模型已被知识库使用，不能删除")
    await db.delete(model)
    await db.commit()


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseRead])
async def list_knowledge_bases(db: AsyncSession = Depends(get_db)) -> list[Collection]:
    statement = (
        select(Collection)
        .options(
            selectinload(Collection.documents).selectinload(Document.files),
            selectinload(Collection.chunking_method),
            selectinload(Collection.embedding_model),
        )
        .order_by(Collection.updated_at.desc())
    )
    return list((await db.scalars(statement)).unique().all())


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate, db: AsyncSession = Depends(get_db)
) -> Collection:
    """Snapshot chunks for every selected MD before any future embedding stage."""

    method = await db.get(ChunkingMethod, payload.chunking_method_id)
    if method is None:
        raise HTTPException(status_code=404, detail="所选分块方法不存在")
    model = await db.get(EmbeddingModel, payload.embedding_model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="所选嵌入模型不存在")

    document_ids = list(dict.fromkeys(payload.document_ids))
    documents = list(
        (
            await db.scalars(
                select(Document)
                .options(selectinload(Document.files))
                .where(Document.id.in_(document_ids))
            )
        ).all()
    )
    if len(documents) != len(document_ids):
        raise HTTPException(status_code=404, detail="部分所选文献不存在，请刷新后重试")

    missing_markdown = [
        document.title
        for document in documents
        if not any(file.kind == "markdown" for file in document.files)
    ]
    if missing_markdown:
        raise HTTPException(
            status_code=409,
            detail=f"以下文献尚无 MD 版本：{'、'.join(missing_markdown[:5])}",
        )

    collection = Collection(
        name=_normalized_name(payload.name),
        description=payload.description,
        chunking_method=method,
        embedding_model=model,
        status="building",
        documents=documents,
    )
    db.add(collection)
    try:
        await db.flush()
        normalized_config = validate_chunking_config(
            method.strategy, method.config or {}
        ).model_dump()
        # Chunks are committed as a single transaction: a partially built knowledge
        # base is never exposed if one document fails validation or splitting.
        for document in documents:
            markdown_file = next(file for file in document.files if file.kind == "markdown")
            _, chunks = build_chunks(
                await _document_text(document), method.strategy, normalized_config
            )
            if not chunks:
                raise HTTPException(
                    status_code=422,
                    detail=f"文献“{document.title}”的 MD 内容为空，无法构建知识库",
                )
            for chunk in chunks:
                db.add(
                    Chunk(
                        collection_id=collection.id,
                        document_id=document.id,
                        document_file_id=markdown_file.id,
                        chunk_index=chunk.index,
                        index_version=f"collection:{collection.id}",
                        content=chunk.content,
                        embedding_text=chunk.embedding_text,
                        heading_path=chunk.heading_path,
                        char_start=chunk.char_start,
                        char_end=chunk.char_end,
                        metadata_json={
                            "chunking_method_id": str(method.id),
                            "strategy": method.strategy,
                            "config": normalized_config,
                        },
                    )
                )
        collection.status = "chunked"
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="知识库名称已存在") from exc
    result = await _get_knowledge_base(db, collection.id)
    if result is None:
        raise HTTPException(status_code=500, detail="知识库创建后读取失败")
    return result


@router.get("/knowledge-bases/{collection_id}", response_model=KnowledgeBaseRead)
async def get_knowledge_base(
    collection_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> Collection:
    collection = await _get_knowledge_base(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return collection


@router.post(
    "/knowledge-bases/{collection_id}/build",
    response_model=ProcessingTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def build_knowledge_base(
    collection_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ProcessingTask:
    collection = await db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    task, created = await ProcessingTaskRepository(db).enqueue(
        task_type="knowledge_base_build",
        resource_type="knowledge_base",
        resource_id=collection.id,
        resource_name=collection.name,
    )
    if created:
        collection.status = "building"
        await db.commit()
    return task


@router.post(
    "/knowledge-bases/{collection_id}/search",
    response_model=KnowledgeSearchResponse,
)
async def search_knowledge_base(
    collection_id: uuid.UUID,
    payload: KnowledgeSearchRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    try:
        return await search_collection(
            db,
            collection_id,
            payload.query,
            top_k=payload.top_k,
            minimum_score=payload.minimum_score,
            strategy=payload.strategy,
            use_reranker=payload.use_reranker,
            index_version=payload.index_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/knowledge-bases/{collection_id}/index-versions",
    response_model=IndexVersionList,
)
async def list_index_versions(
    collection_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    collection = await db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    items = list(
        (
            await db.scalars(
                select(IndexVersion)
                .where(IndexVersion.collection_id == collection_id)
                .order_by(IndexVersion.created_at.desc())
            )
        ).all()
    )
    active = next((item.version for item in items if item.status == "active"), None)
    return {"active_version": active, "items": items}


@router.post(
    "/knowledge-bases/{collection_id}/index-versions/{version_id}/activate",
    response_model=IndexVersionList,
)
async def activate_index_version(
    collection_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    target = await db.scalar(
        select(IndexVersion).where(
            IndexVersion.id == version_id,
            IndexVersion.collection_id == collection_id,
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="索引版本不存在")
    if target.status not in {"active", "ready", "superseded"}:
        raise HTTPException(status_code=409, detail="只有已构建完成的索引版本可以激活")
    await db.execute(
        update(IndexVersion)
        .where(
            IndexVersion.collection_id == collection_id,
            IndexVersion.status == "active",
            IndexVersion.id != target.id,
        )
        .values(status="superseded")
    )
    target.status = "active"
    target.activated_at = datetime.now(UTC)
    await db.commit()
    return await list_index_versions(collection_id, db)


@router.delete("/knowledge-bases/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    collection_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> None:
    collection = await db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    await db.delete(collection)
    await db.commit()


@router.get(
    "/knowledge-bases/{collection_id}/documents/{document_id}/chunks",
    response_model=DocumentChunkPreview,
)
async def preview_document_chunks(
    collection_id: uuid.UUID,
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DocumentChunkPreview:
    collection = await _get_knowledge_base(db, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    document = next((item for item in collection.documents if item.id == document_id), None)
    if document is None:
        raise HTTPException(status_code=404, detail="该文献不在此知识库中")
    if collection.chunking_method is None:
        raise HTTPException(status_code=409, detail="知识库尚未配置分块方法")

    persisted = list(
        (
            await db.scalars(
                select(Chunk)
                .where(
                    Chunk.collection_id == collection.id,
                    Chunk.document_id == document.id,
                )
                .order_by(Chunk.chunk_index)
            )
        ).all()
    )
    if persisted:
        chunks = [
            ChunkPreviewItem(
                index=chunk.chunk_index or index,
                content=chunk.content,
                embedding_text=chunk.embedding_text or chunk.content,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                char_count=len(chunk.content),
                heading_path=chunk.heading_path or [],
            )
            for index, chunk in enumerate(persisted, start=1)
        ]
    else:
        chunks = build_chunk_preview(
            await _document_text(document),
            collection.chunking_method.strategy,
            collection.chunking_method.config or {},
        )
    return DocumentChunkPreview(
        document_id=document.id,
        document_title=document.title,
        method=collection.chunking_method,
        chunks=chunks,
        stats=_chunk_stats(chunks, collection.chunking_method.config or {}),
    )
