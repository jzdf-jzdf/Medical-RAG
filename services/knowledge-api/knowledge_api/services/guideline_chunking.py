"""Integrate the complete, pinned Guideline Chunker pipeline with native RAG chunks."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from knowledge_api.core.config import get_settings
from knowledge_api.services.chunking import ChunkResult, build_chunks
from knowledge_api.vendor.guideline_chunker.batching import plan_batches
from knowledge_api.vendor.guideline_chunker.config import (
    BatchingConfig,
    ChunkingConfig,
    DeepSeekConfig,
    SummaryConfig,
    load_pipeline_config,
)
from knowledge_api.vendor.guideline_chunker.deepseek import DeepSeekSemanticClient
from knowledge_api.vendor.guideline_chunker.markdown import parse_source_document
from knowledge_api.vendor.guideline_chunker.pipeline import run_document
from knowledge_api.vendor.guideline_chunker.storage import load_audit, load_chunks, load_processing_report
from knowledge_api.vendor.guideline_chunker.taxonomy import load_taxonomy

UPSTREAM_REVISION = "13940c9709dac157f846eb072574395084eabb48"
ADAPTER_PROMPT_VERSION = "source-faithful-summary-v1"
DATA_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "guideline_chunker_data"
ProgressCallback = Callable[[str, int | None, str], Awaitable[None]]


class _PipelineCancelled(BaseException):
    """Stop the thread between requests without treating shutdown as model failure."""


class GuidelineChunkingError(ValueError):
    def __init__(self, message: str, run_id: str):
        super().__init__(f"{message}，运行 ID：{run_id}")
        self.run_id = run_id


class GuidelineChunkingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    batching: BatchingConfig = Field(default_factory=BatchingConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    cache_enabled: bool = True
    require_quality_gate: bool = True
    prepend_heading_path: bool = True
    max_chunks: int = Field(default=10000, ge=1, le=10000)


def engine_status() -> dict:
    settings = get_settings()
    taxonomy = load_taxonomy(DATA_ROOT / "topics_hich_v1.yaml")
    return {
        "strategy": "guideline_semantic",
        "version": "0.1.0",
        "revision": UPSTREAM_REVISION,
        "model": GuidelineChunkingConfig().deepseek.model,
        "api_key_configured": bool(settings.deepseek_api_key),
        "topic_count": len(taxonomy.allowed_tags),
        "default_config": GuidelineChunkingConfig().model_dump(mode="json"),
    }


def run_directory(run_id: str) -> Path:
    # Run identifiers never allow user-supplied file paths into artifact routes.
    parsed = uuid.UUID(run_id)
    return get_settings().workspace_root.resolve() / "guideline-chunker" / "runs" / parsed.hex


def run_artifact(run_id: str, name: str) -> Path:
    if name not in {
        "source.md",
        "chunks.jsonl",
        "manifest.json",
        "audit.jsonl",
        "processing_report.json",
    }:
        raise ValueError("不支持的指南切块产物")
    root = run_directory(run_id)
    context = json.loads((root / "context.json").read_text(encoding="utf-8"))
    source_id = uuid.UUID(context["document_id"]).hex
    if name == "source.md":
        return root / f"{source_id}.md"
    return root / "output" / source_id / name


def artifact_urls(run_id: str) -> dict[str, str]:
    return {
        name: f"/api/v1/guideline-chunking/runs/{run_id}/artifacts/{name}"
        for name in (
            "source.md",
            "chunks.jsonl",
            "manifest.json",
            "audit.jsonl",
            "processing_report.json",
        )
    }


def run_summary(run_id: str) -> dict:
    report = json.loads(run_artifact(run_id, "processing_report.json").read_text(encoding="utf-8"))
    context = json.loads((run_directory(run_id) / "context.json").read_text(encoding="utf-8"))
    return {
        "run_id": run_id,
        "document_title": context["title"],
        "chunks": [],
        "run_metadata": {"run_id": run_id, "quality_gate_passed": report["quality_gate_passed"]},
        "processing_report": report,
        "artifact_urls": artifact_urls(run_id),
        "manifest": json.loads(run_artifact(run_id, "manifest.json").read_text(encoding="utf-8")),
    }


def _run_pipeline(
    text: str,
    config: GuidelineChunkingConfig,
    document_id: uuid.UUID,
    document_title: str,
    preview: bool,
    notify: Callable[[int | None, str], None],
) -> list[ChunkResult]:
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise ValueError("指南语义分块需要在知识服务和 Worker 后端配置 DEEPSEEK_API_KEY")
    run_id = uuid.uuid4().hex
    root = run_directory(run_id)
    root.mkdir(parents=True, exist_ok=False)
    source = root / f"{document_id.hex}.md"
    source.write_bytes(text.encode("utf-8"))
    (root / "context.json").write_text(
        json.dumps({"document_id": str(document_id), "title": document_title}), encoding="utf-8"
    )
    pipeline_config = load_pipeline_config(DATA_ROOT / "hich_v1.yaml")
    pipeline_config.deepseek = config.deepseek
    pipeline_config.batching = config.batching
    pipeline_config.chunking = config.chunking
    pipeline_config.summary = config.summary
    pipeline_config.cache.enabled = config.cache_enabled
    pipeline_config.cache.directory = (
        settings.workspace_root.resolve() / "guideline-chunker" / "cache" / ADAPTER_PROMPT_VERSION
    )
    import yaml

    config_path = root / "pipeline.yaml"
    config_path.write_text(
        yaml.safe_dump(pipeline_config.model_dump(mode="json"), allow_unicode=True),
        encoding="utf-8",
    )
    pipeline_config = load_pipeline_config(config_path)
    taxonomy = load_taxonomy(pipeline_config.taxonomy.path)
    document = parse_source_document(source)
    batches = plan_batches(document.atoms, config.batching)
    seen: set[str] = set()

    from openai import OpenAI

    transport = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
    semantic_client = DeepSeekSemanticClient(pipeline_config, taxonomy, client=transport)

    class ProgressClient:
        @property
        def last_exchange(self):
            return semantic_client.last_exchange

        def analyze(self, batch, atoms, *, retry_feedback=None):
            seen.add(batch.batch_id)
            notify(
                round((len(seen) - 1) / max(1, len(batches)) * 90),
                f"DeepSeek 正在判断第 {len(seen)}/{len(batches)} 个语义批次",
            )
            if retry_feedback and "SUMMARY_" in retry_feedback:
                retry_feedback += (
                    "。摘要校验比较完整动作及其强度：不要删除原文的推荐意见引导语、"
                    "应/建议/不建议等标识、动作对象及推荐等级。若知识块原文不超过"
                    f"{config.summary.max_chars}字，可直接将原文整句作为summary，"
                    "保留全部引导语和限定词；不要为了简写而改变动作表述。"
                )
            return semantic_client.analyze(batch, atoms, retry_feedback=retry_feedback)

    try:
        result = run_document(source, root / "output", pipeline_config, ProgressClient())
    finally:
        transport.close()
    if result.output_paths is None:
        raise ValueError(f"指南切块未生成正式产物，运行 ID：{run_id}")
    outputs = result.output_paths
    chunks = [chunk.model_dump(mode="json") for chunk in load_chunks(outputs.chunks)]
    audits = [record.model_dump(mode="json") for record in load_audit(outputs.audit)]
    report = load_processing_report(outputs.processing_report).model_dump(mode="json")
    if len(chunks) > config.max_chunks:
        raise GuidelineChunkingError(f"指南切块超过 {config.max_chunks} 块上限", run_id)
    passed = bool(report.get("quality_gate_passed"))
    if not preview and config.require_quality_gate and not passed:
        raise GuidelineChunkingError(
            "指南切块未通过质量门，禁止发布索引，请检查质量报告和切块审计", run_id
        )
    if not chunks:
        raise GuidelineChunkingError("指南未生成可用知识块，请查看质量报告", run_id)
    atoms = {atom.atom_id: atom for atom in document.atoms}
    accepted = {
        record["chunk_id"]: record
        for record in audits
        if record.get("chunk_id") and record["decision"] == "accepted"
    }
    results = []
    for index, chunk in enumerate(chunks, 1):
        audit = accepted.get(chunk["chunk_id"])
        if audit is None:
            raise ValueError(f"指南知识块缺少原文范围审计，运行 ID：{run_id}")
        start = atoms[audit["start_atom_id"]].start_char
        end = atoms[audit["end_atom_id"]].end_char
        if text[start:end] != chunk["content"]:
            raise ValueError(f"指南知识块与原文字符范围不一致，运行 ID：{run_id}")
        path = chunk["section_path"]
        embedding_text = (
            f"{' > '.join(path)}\n\n{chunk['content']}"
            if config.prepend_heading_path and path
            else chunk["content"]
        )
        metadata = {
            **{
                key: value for key, value in chunk.items() if key not in {"content", "section_path"}
            },
            "document_id": str(document_id),
            "document_title": document_title,
            "run_id": run_id,
            "run_status": result.status.value,
            "quality_gate_passed": passed,
            "upstream_revision": UPSTREAM_REVISION,
            "adapter_prompt_version": ADAPTER_PROMPT_VERSION,
        }
        results.append(
            ChunkResult(index, chunk["content"], embedding_text, start, end, path, metadata)
        )
    notify(100, f"指南原子知识切块完成：{len(results)} 块，质量门{'通过' if passed else '未通过'}")
    return results


async def build_chunks_async(
    text: str,
    strategy: str,
    raw_config: dict,
    *,
    document_id: uuid.UUID,
    document_title: str,
    preview: bool = False,
    on_progress: ProgressCallback | None = None,
):
    if strategy != "guideline_semantic":
        return build_chunks(text, strategy, raw_config)
    config = GuidelineChunkingConfig.model_validate(raw_config)
    loop = asyncio.get_running_loop()
    stop_event = threading.Event()

    def notify(progress: int | None, message: str) -> None:
        if stop_event.is_set():
            raise _PipelineCancelled()
        if on_progress:
            asyncio.run_coroutine_threadsafe(
                on_progress("semantic_chunking", progress, message), loop
            ).result()

    # Shield and drain on shutdown: a cancelled coroutine must not leave a
    # pipeline thread writing artifacts after the Worker has requeued the job.
    job = asyncio.create_task(
        asyncio.to_thread(
            _run_pipeline,
            text,
            config,
            document_id,
            document_title,
            preview,
            notify,
        )
    )
    try:
        chunks = await asyncio.shield(job)
    except asyncio.CancelledError:
        stop_event.set()
        try:
            await job
        except _PipelineCancelled:
            pass
        raise
    return config, chunks
