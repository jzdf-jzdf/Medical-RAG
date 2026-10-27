"""Version-aware Qdrant + BM25 retrieval with RRF and optional Cross-Encoder reranking."""

from __future__ import annotations

import asyncio
import math
import re
import time
import uuid
from collections import Counter
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models import Chunk, Collection, Document, IndexVersion
from app.services.embeddings import embed_texts
from app.services.qdrant_store import search as qdrant_search


def _tokenize(value: str) -> list[str]:
    lowered = value.lower()
    tokens = re.findall(r"[a-z0-9_]+", lowered)
    for sequence in re.findall(r"[\u3400-\u9fff]+", lowered):
        tokens.extend(
            sequence
            if len(sequence) == 1
            else (sequence[index : index + 2] for index in range(len(sequence) - 1))
        )
    return tokens


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(v * v for v in left)) * math.sqrt(sum(v * v for v in right))
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator if denominator else 0.0


def _lexical_score(query: str, text: str) -> float:
    terms = set(_tokenize(query))
    if not terms:
        return 0.0
    return len(terms & set(_tokenize(text))) / len(terms)


def _bm25_scores(query: str, documents: dict[str, str]) -> dict[str, float]:
    query_terms = _tokenize(query)
    if not query_terms or not documents:
        return {}
    tokenized = {key: _tokenize(text) for key, text in documents.items()}
    average_length = sum(len(tokens) for tokens in tokenized.values()) / len(tokenized)
    document_frequency = Counter(term for tokens in tokenized.values() for term in set(tokens))
    scores: dict[str, float] = {}
    k1, b = 1.5, 0.75
    for key, tokens in tokenized.items():
        frequencies = Counter(tokens)
        score = 0.0
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            count = document_frequency[term]
            idf = math.log(1 + (len(tokenized) - count + 0.5) / (count + 0.5))
            denominator = frequency + k1 * (
                1 - b + b * len(tokens) / max(average_length, 1)
            )
            score += idf * frequency * (k1 + 1) / denominator
        if score > 0:
            scores[key] = score
    return scores


def _rank(scores: dict[str, float], limit: int) -> list[str]:
    return [key for key, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]]


def _rrf(rankings: list[list[str]], k: int) -> dict[str, float]:
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            fused[key] = fused.get(key, 0.0) + 1 / (k + rank)
    return fused


@lru_cache(maxsize=2)
def _load_reranker(model_name: str, device: str):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    resolved_device = device
    if device == "auto":
        resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    model.to(resolved_device)
    model.eval()
    return tokenizer, model, resolved_device


def _cross_encoder_scores(query: str, texts: list[str]) -> list[float]:
    settings = get_settings()
    tokenizer, model, device = _load_reranker(
        settings.reranker_model, settings.reranker_device
    )
    encoded = tokenizer(
        [[query, text] for text in texts],
        padding=True,
        truncation=True,
        max_length=settings.reranker_max_length,
        return_tensors="pt",
    ).to(device)
    import torch

    with torch.inference_mode():
        logits = model(**encoded).logits
    values = logits[:, 0] if logits.ndim == 2 else logits
    return [float(value) for value in values.detach().cpu().tolist()]


async def search_collection(
    db: AsyncSession,
    collection_id: uuid.UUID,
    query: str,
    *,
    top_k: int = 8,
    minimum_score: float = 0.0,
    strategy: str = "hybrid",
    use_reranker: bool = True,
    index_version: str | None = None,
) -> dict[str, object]:
    if strategy not in {"hybrid", "dense", "bm25"}:
        raise ValueError("检索策略必须是 hybrid、dense 或 bm25")
    trace_id = str(uuid.uuid4())
    trace: list[dict[str, Any]] = []
    started = time.perf_counter()
    collection = await db.scalar(
        select(Collection)
        .options(selectinload(Collection.embedding_model))
        .where(Collection.id == collection_id)
    )
    if collection is None:
        raise ValueError("知识库不存在")
    if collection.embedding_model is None:
        raise ValueError("知识库尚未配置嵌入模型")

    version_query = select(IndexVersion).where(IndexVersion.collection_id == collection_id)
    version_query = version_query.where(
        IndexVersion.version == index_version
        if index_version
        else IndexVersion.status == "active"
    )
    index = await db.scalar(version_query.order_by(IndexVersion.created_at.desc()).limit(1))
    if index is None or index.status not in {"active", "ready", "superseded"}:
        raise ValueError("知识库尚未完成可用索引构建")

    rows = (
        await db.execute(
            select(Chunk, Document)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.collection_id == collection_id,
                Chunk.index_version == index.version,
                Chunk.embedding.is_not(None),
            )
            .limit(get_settings().search_candidate_limit)
        )
    ).all()
    if not rows:
        raise ValueError("当前索引版本没有可检索分块")
    by_id = {str(chunk.id): (chunk, document) for chunk, document in rows}
    candidate_limit = min(len(rows), max(top_k * 8, 60))

    dense_scores: dict[str, float] = {}
    if strategy != "bm25":
        phase = time.perf_counter()
        query_vector = (await embed_texts(collection.embedding_model, [query], purpose="query"))[0]
        qdrant_name = str((index.config or {}).get("qdrant_collection") or "")
        backend = "qdrant"
        try:
            if not qdrant_name:
                raise RuntimeError("索引未记录 Qdrant 集合")
            hits = await qdrant_search(qdrant_name, query_vector, candidate_limit)
            for hit in hits:
                chunk_id = str((hit.get("payload") or {}).get("chunk_id") or hit.get("id"))
                if chunk_id in by_id:
                    dense_scores[chunk_id] = max(0.0, float(hit.get("score") or 0.0))
        except Exception as exc:  # noqa: BLE001 - Qdrant outage must degrade to DB vectors.
            backend = "postgres-fallback"
            for key, (chunk, _) in by_id.items():
                dense_scores[key] = max(
                    0.0,
                    _cosine(query_vector, [float(value) for value in (chunk.embedding or [])]),
                )
            trace.append({"stage": "dense_warning", "detail": str(exc)[:300]})
        trace.append(
            {
                "stage": "dense",
                "backend": backend,
                "candidates": len(dense_scores),
                "latency_ms": round((time.perf_counter() - phase) * 1000, 2),
            }
        )

    bm25_scores: dict[str, float] = {}
    if strategy != "dense":
        phase = time.perf_counter()
        bm25_scores = _bm25_scores(
            query,
            {
                key: f"{document.title}\n{chunk.content}"
                for key, (chunk, document) in by_id.items()
            },
        )
        trace.append(
            {
                "stage": "bm25",
                "backend": "postgres",
                "candidates": len(bm25_scores),
                "latency_ms": round((time.perf_counter() - phase) * 1000, 2),
            }
        )

    phase = time.perf_counter()
    rankings = []
    if dense_scores:
        rankings.append(_rank(dense_scores, candidate_limit))
    if bm25_scores:
        rankings.append(_rank(bm25_scores, candidate_limit))
    fused = _rrf(rankings, get_settings().rrf_k)
    ordered = _rank(fused, candidate_limit)
    trace.append(
        {
            "stage": "rrf",
            "k": get_settings().rrf_k,
            "candidates": len(ordered),
            "latency_ms": round((time.perf_counter() - phase) * 1000, 2),
        }
    )

    rerank_scores: dict[str, float] = {}
    reranker_model = get_settings().reranker_model.strip()
    if strategy == "hybrid" and use_reranker and reranker_model and ordered:
        phase = time.perf_counter()
        try:
            raw = await asyncio.to_thread(
                _cross_encoder_scores,
                query,
                [by_id[key][0].content for key in ordered],
            )
            rerank_scores = dict(zip(ordered, raw, strict=True))
            ordered = _rank(rerank_scores, candidate_limit)
            trace.append(
                {
                    "stage": "cross_encoder",
                    "model": reranker_model,
                    "candidates": len(ordered),
                    "latency_ms": round((time.perf_counter() - phase) * 1000, 2),
                }
            )
        except Exception as exc:  # noqa: BLE001 - reranker outage must preserve RRF results.
            trace.append(
                {
                    "stage": "cross_encoder",
                    "model": reranker_model,
                    "status": "fallback",
                    "detail": str(exc)[:300],
                    "latency_ms": round((time.perf_counter() - phase) * 1000, 2),
                }
            )
    else:
        trace.append(
            {
                "stage": "cross_encoder",
                "status": "disabled"
                if not reranker_model or not use_reranker
                else "not_applicable",
            }
        )

    ranking_basis = rerank_scores or fused or dense_scores or bm25_scores
    values = [ranking_basis.get(key, 0.0) for key in ordered]
    low, high = min(values, default=0.0), max(values, default=0.0)
    hits: list[dict[str, object]] = []
    for rank, key in enumerate(ordered, start=1):
        chunk, document = by_id[key]
        raw_score = ranking_basis.get(key, 0.0)
        normalized = (
            1.0
            if high == low and high > 0
            else ((raw_score - low) / (high - low) if high > low else 0.0)
        )
        if normalized < minimum_score:
            continue
        hits.append(
            {
                "chunk_id": key,
                "document_id": str(document.id),
                "document_title": document.title,
                "content": chunk.content,
                "heading_path": chunk.heading_path or [],
                "metadata": getattr(chunk, "metadata_json", None) or {},
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "score": round(normalized, 6),
                "vector_score": round(dense_scores[key], 6) if key in dense_scores else None,
                "lexical_score": round(bm25_scores[key], 6) if key in bm25_scores else None,
                "rrf_score": round(fused[key], 8) if key in fused else None,
                "rerank_score": round(rerank_scores[key], 6) if key in rerank_scores else None,
                "rank": rank,
                "index_version": index.version,
                "citation": {
                    "title": document.title,
                    "heading": " › ".join(chunk.heading_path or []),
                    "pages": [chunk.page_start, chunk.page_end],
                    "chunk_id": key,
                    "document_id": str(document.id),
                    "index_version": index.version,
                },
            }
        )
        if len(hits) >= top_k:
            break
    trace.append(
        {
            "stage": "complete",
            "returned": len(hits),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    )
    return {
        "collection_id": str(collection.id),
        "collection_name": collection.name,
        "query": query,
        "count": len(hits),
        "items": hits,
        "strategy": strategy,
        "index_version": index.version,
        "trace_id": trace_id,
        "trace": trace,
    }
