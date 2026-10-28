"""Embedding providers, including the official local MedCPT dual encoder."""

import asyncio
import hashlib
import math
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import httpx

from knowledge_api.core.config import get_settings
from knowledge_api.models import EmbeddingModel

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]")
EmbeddingPurpose = Literal["query", "document"]
DownloadProgress = Callable[[str, int, str], Awaitable[None]]


class EmbeddingError(RuntimeError):
    """Raised when a configured embedding provider cannot produce vectors."""


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def builtin_embedding(text: str, dimension: int) -> list[float]:
    """Create a deterministic signed feature-hash vector for tests/fallbacks."""

    if dimension < 8:
        raise EmbeddingError("内置嵌入维度不能小于 8")
    tokens = TOKEN_PATTERN.findall(text.lower())
    features = tokens + ["".join(tokens[index : index + 2]) for index in range(len(tokens) - 1)]
    vector = [0.0] * dimension
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
        bucket = int.from_bytes(digest[:8], "big") % dimension
        sign = 1.0 if digest[8] & 1 else -1.0
        vector[bucket] += sign
    return _normalize(vector)


def _medcpt_ids(model: EmbeddingModel) -> tuple[str, str]:
    settings = get_settings()
    config = model.config or {}
    query_id = str(config.get("query_model") or settings.medcpt_query_model)
    article_id = str(config.get("article_model") or model.model_name or settings.medcpt_article_model)
    return query_id, article_id


def _medcpt_cache_dir(model: EmbeddingModel) -> Path:
    configured = str((model.config or {}).get("cache_dir") or "").strip()
    return Path(configured) if configured else get_settings().medcpt_cache_dir


def _medcpt_local_dir(model: EmbeddingModel, purpose: EmbeddingPurpose) -> Path:
    folder = "query-encoder" if purpose == "query" else "article-encoder"
    return _medcpt_cache_dir(model).resolve() / folder


async def download_medcpt_model(
    model: EmbeddingModel,
    on_progress: DownloadProgress | None = None,
) -> dict[str, object]:
    """Download both official encoders into the configured Hugging Face cache."""

    if model.provider.lower().strip() != "medcpt":
        raise EmbeddingError("只有 MedCPT 注册项支持本地双编码器下载")
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise EmbeddingError("缺少 huggingface-hub，请先安装服务依赖") from exc

    query_id, article_id = _medcpt_ids(model)
    cache_dir = _medcpt_cache_dir(model).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    downloaded: dict[str, str] = {}
    for role, repo_id, progress in (
        ("query", query_id, 15),
        ("document", article_id, 55),
    ):
        if on_progress:
            label = "查询编码器" if role == "query" else "文献编码器"
            await on_progress("downloading", progress, f"正在下载 MedCPT {label}")
        try:
            path = await asyncio.to_thread(
                snapshot_download,
                repo_id=repo_id,
                local_dir=str(_medcpt_local_dir(model, role)),
                ignore_patterns=["pytorch_model.bin"],
            )
        except Exception as exc:
            raise EmbeddingError(f"MedCPT 模型下载失败（{repo_id}）：{exc}") from exc
        downloaded["query" if role == "query" else "article"] = path
    if on_progress:
        await on_progress("verifying", 90, "正在校验并加载 MedCPT 模型")
    await _get_medcpt_runtime(model, "query")
    await _get_medcpt_runtime(model, "document")
    return {
        "provider": "medcpt",
        "query_model": query_id,
        "article_model": article_id,
        "dimension": 768,
        "cache_dir": str(cache_dir),
        "snapshots": downloaded,
    }


_medcpt_runtimes: dict[tuple[str, str, str], tuple[object, object, str]] = {}
_medcpt_load_lock = asyncio.Lock()


def _load_medcpt_runtime(repo_id: str, local_dir: Path, requested_device: str):
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise EmbeddingError("缺少 torch/transformers，请先安装服务依赖") from exc

    if requested_device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        device = requested_device
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            str(local_dir), local_files_only=True
        )
        runtime = AutoModel.from_pretrained(
            str(local_dir), local_files_only=True
        )
        runtime.to(device)
        runtime.eval()
    except Exception as exc:
        raise EmbeddingError(
            f"MedCPT 模型尚未下载或无法加载（{repo_id}），请先执行模型下载：{exc}"
        ) from exc
    return tokenizer, runtime, device


async def _get_medcpt_runtime(model: EmbeddingModel, purpose: EmbeddingPurpose):
    query_id, article_id = _medcpt_ids(model)
    repo_id = query_id if purpose == "query" else article_id
    local_dir = _medcpt_local_dir(model, purpose)
    requested_device = str((model.config or {}).get("device") or get_settings().medcpt_device)
    key = (repo_id, str(local_dir), requested_device)
    runtime = _medcpt_runtimes.get(key)
    if runtime is not None:
        return runtime
    async with _medcpt_load_lock:
        runtime = _medcpt_runtimes.get(key)
        if runtime is None:
            runtime = await asyncio.to_thread(
                _load_medcpt_runtime, repo_id, local_dir, requested_device
            )
            _medcpt_runtimes[key] = runtime
    return runtime


def _article_pairs(texts: list[str]) -> list[list[str]]:
    pairs: list[list[str]] = []
    for text in texts:
        lines = [line.strip(" #\t") for line in text.splitlines() if line.strip()]
        title = lines[0][:500] if lines else "Medical document"
        body = "\n".join(lines[1:]) if len(lines) > 1 else (lines[0] if lines else text)
        pairs.append([title, body])
    return pairs


def _encode_medcpt(runtime, texts: list[str], purpose: EmbeddingPurpose) -> list[list[float]]:
    import torch

    tokenizer, model, device = runtime
    inputs = texts if purpose == "query" else _article_pairs(texts)
    maximum = 64 if purpose == "query" else 512
    encoded = tokenizer(
        inputs,
        truncation=True,
        padding=True,
        return_tensors="pt",
        max_length=maximum,
    )
    encoded = {name: value.to(device) for name, value in encoded.items()}
    with torch.inference_mode():
        vectors = model(**encoded).last_hidden_state[:, 0, :]
        vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
    return vectors.detach().cpu().float().tolist()


async def _embed_medcpt(
    model: EmbeddingModel, texts: list[str], purpose: EmbeddingPurpose
) -> list[list[float]]:
    runtime = await _get_medcpt_runtime(model, purpose)
    batch_size = int((model.config or {}).get("batch_size") or get_settings().medcpt_batch_size)
    result: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        result.extend(
            await asyncio.to_thread(
                _encode_medcpt, runtime, texts[start : start + batch_size], purpose
            )
        )
    return result


async def embed_texts(
    model: EmbeddingModel,
    texts: list[str],
    *,
    purpose: EmbeddingPurpose = "document",
) -> list[list[float]]:
    """Embed texts with the correct side of a dual encoder."""

    if not texts:
        return []
    provider = model.provider.lower().strip()
    dimension = model.dimension or 384
    if provider == "medcpt":
        return await _embed_medcpt(model, texts, purpose)
    if provider in {"builtin", "hash", "offline"}:
        return [builtin_embedding(text, dimension) for text in texts]

    if provider not in {"openai", "openai-compatible", "local"}:
        raise EmbeddingError(
            f"不支持嵌入提供方“{model.provider}”；可使用 medcpt、builtin 或 OpenAI-compatible"
        )
    if not model.base_url:
        raise EmbeddingError("当前嵌入模型没有配置 base_url")

    settings = get_settings()
    endpoint = model.base_url.rstrip("/")
    if not endpoint.endswith("/embeddings"):
        endpoint = f"{endpoint}/embeddings"
    config = model.config or {}
    api_key = settings.embedding_api_key
    api_key_env = str(config.get("api_key_env") or "").strip()
    if api_key_env:
        api_key = os.getenv(api_key_env, api_key)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=settings.embedding_timeout_seconds) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json={"model": model.model_name, "input": texts},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EmbeddingError(f"嵌入服务调用失败：{exc}") from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != len(texts):
        raise EmbeddingError("嵌入服务返回的数据数量与输入不一致")
    rows = sorted(rows, key=lambda item: int(item.get("index", 0)))
    vectors = [item.get("embedding") for item in rows]
    if not all(isinstance(vector, list) and vector for vector in vectors):
        raise EmbeddingError("嵌入服务响应缺少 embedding 数组")
    normalized = [[float(value) for value in vector] for vector in vectors]
    if model.dimension and any(len(vector) != model.dimension for vector in normalized):
        raise EmbeddingError("嵌入服务返回的向量维度与注册配置不一致")
    return [_normalize(vector) for vector in normalized]
