"""按指南独立输出，并支持基于缓存续跑的编排。"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .assembly import AssemblyError, Chunk, DocumentChunkIdRegistry, assemble_batch
from .batching import find_oversized_atoms, plan_batches
from .cache import CacheStore, build_cache_entry, build_cache_key
from .config import PipelineConfig, load_pipeline_config
from .deepseek import RetryExhaustedError, SemanticClient, analyze_with_retry
from .markdown import parse_source_document
from .models import (
    AuditDecision,
    AuditRecord,
    ChunkType,
    RecommendationCoverageStatus,
    RunStatus,
    SemanticBatch,
    SemanticBatchResponse,
    SemanticChunkDecision,
    SemanticRangeDecision,
    SourceAtom,
    SourceDocument,
)
from .recommendations import mark_recommendation_candidates
from .reporting import (
    DeepSeekStatistics,
    build_configuration_identity,
    build_manifest,
    build_processing_report,
    sha256_file,
)
from .storage import OutputPaths, atomic_write_json, write_document_outputs
from .taxonomy import TopicTaxonomy, load_taxonomy
from .validation import ValidationIssue, validate_document_chunks, validate_semantic_response


STATE_NAME = ".run_state.json"

logger = logging.getLogger(__name__)


class InputConflictError(ValueError):
    """当输入/输出布局不确定或不安全时抛出。"""


@dataclass(frozen=True, slots=True)
class DocumentRunResult:
    source_doc: str
    status: RunStatus
    output_dir: Path
    output_paths: OutputPaths | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BatchRunResult:
    documents: list[DocumentRunResult]

    @property
    def by_name(self) -> dict[str, DocumentRunResult]:
        return {result.source_doc: result for result in self.documents}


def discover_inputs(
    path: Path,
    pattern: str = "*.md",
    recursive: bool = False,
) -> list[Path]:
    """查找待处理的 Markdown 指南，并保证返回顺序和输出目录名称无冲突。

    ``path`` 既可以指向单个文件，也可以指向目录。目录模式下，
    ``recursive`` 决定只搜索当前目录还是递归搜索所有子目录。
    """

    # 转换为绝对规范路径，避免后续逻辑受到当前工作目录或 ``..`` 的影响。
    source = Path(path).resolve()
    if source.is_file():
        # 单文件输入不需要执行目录搜索，但后面仍会统一检查 .md 扩展名。
        candidates = [source]
    elif source.is_dir():
        # glob 只搜索当前目录；rglob 会递归搜索所有下级目录。
        iterator = source.rglob(pattern) if recursive else source.glob(pattern)
        # 搜索模式也可能匹配目录，因此这里只保留真正的文件。
        candidates = [candidate.resolve() for candidate in iterator if candidate.is_file()]
    else:
        # 输入既不是现有文件也不是现有目录时立即失败，不返回空结果掩盖错误。
        raise FileNotFoundError(source)

    # 即使用户传入更宽泛的 pattern，也只接受 .md 文件。
    # 显式排序保证不同文件系统、不同运行次数下的处理顺序保持一致。
    guides = sorted(
        (candidate for candidate in candidates if candidate.suffix.lower() == ".md"),
        key=lambda candidate: str(candidate),
    )

    # 每本指南的输出目录使用 ``guide.stem`` 命名。如果不同目录下存在
    # 同名文件，它们会写入同一个输出目录，因此必须在运行前拒绝。
    stems: dict[str, Path] = {}
    duplicates: list[str] = []
    for guide in guides:
        # casefold 比 lower 更适合做不区分大小写的文本比较；
        # 这里也能提前发现 Windows 文件系统上的大小写名称冲突。
        key = guide.stem.casefold()
        if key in stems:
            duplicates.append(guide.stem)
        else:
            stems[key] = guide

    if duplicates:
        # 合并首次出现和后续重复的名称，去重、排序后统一报告给用户。
        names = sorted({stems[name.casefold()].stem for name in duplicates} | set(duplicates))
        raise InputConflictError(f"duplicate output directory stems: {names}")

    return guides


def _cache_root(output_root: Path, config: PipelineConfig) -> Path:
    directory = config.cache.directory
    return directory if directory.is_absolute() else output_root / directory


def _batch_atoms(document: SourceDocument, batch: SemanticBatch) -> list[SourceAtom]:
    wanted = set(
        batch.context_before_ids + batch.core_atom_ids + batch.context_after_ids
    )
    return [atom for atom in document.atoms if atom.atom_id in wanted]


def _range_atoms(
    document: SourceDocument,
    start_atom_id: str,
    end_atom_id: str,
) -> list[SourceAtom]:
    atoms = sorted(document.atoms, key=lambda atom: (atom.start_char, atom.end_char))
    index = {atom.atom_id: position for position, atom in enumerate(atoms)}
    start, end = index[start_atom_id], index[end_atom_id]
    return atoms[start : end + 1]


def _audit(
    *,
    run_id: str,
    document: SourceDocument,
    batch_id: str | None,
    atoms: Sequence[SourceAtom],
    decision: AuditDecision,
    reason_code: str,
    decision_reason: str,
    candidate_type: ChunkType | str | None = None,
    final_type: ChunkType | str | None = None,
    chunk_id: str | None = None,
    recommendation_coverage_status: RecommendationCoverageStatus | str | None = None,
    attempt_count: int = 0,
    validation_errors: Sequence[str] = (),
) -> AuditRecord:
    first, last = atoms[0], atoms[-1]
    is_candidate = any(atom.recommendation_candidate for atom in atoms)
    coverage = recommendation_coverage_status
    if coverage is None:
        coverage = (
            RecommendationCoverageStatus.PENDING_REVIEW
            if is_candidate
            else RecommendationCoverageStatus.NOT_CANDIDATE
        )
    return AuditRecord(
        audit_id=f"AUD_{uuid4().hex.upper()}",
        run_id=run_id,
        source_doc=document.source_doc,
        batch_id=batch_id,
        section_id=first.section_id,
        section_path=list(first.section_path),
        start_atom_id=first.atom_id,
        end_atom_id=last.atom_id,
        source_excerpt=document.text[first.start_char : last.end_char],
        decision=decision,
        reason_code=reason_code,
        decision_reason=decision_reason,
        candidate_type=candidate_type,
        final_type=final_type,
        chunk_id=chunk_id,
        recommendation_candidate=is_candidate,
        recommendation_coverage_status=coverage,
        attempt_count=attempt_count,
        validation_errors=list(validation_errors),
        created_at=datetime.now(timezone.utc),
    )


def _response_audits(
    *,
    run_id: str,
    document: SourceDocument,
    batch: SemanticBatch,
    response: SemanticBatchResponse,
    chunks: Sequence[Chunk],
) -> tuple[list[AuditRecord], set[str], set[str]]:
    """将每个已验证的语义处置结果转换为持久化审计记录。"""

    records: list[AuditRecord] = []
    disposed: set[str] = set(batch.core_atom_ids)
    covered: set[str] = set()
    by_span = {
        (chunk.content, chunk.section_id, chunk.type): chunk for chunk in chunks
    }
    for decision in response.chunks:
        atoms = _range_atoms(document, decision.start_atom_id, decision.end_atom_id)
        chunk = by_span[
            (
                document.text[atoms[0].start_char : atoms[-1].end_char],
                atoms[0].section_id,
                str(getattr(decision.type, "value", decision.type)),
            )
        ]
        if any(atom.recommendation_candidate for atom in atoms):
            covered.update(atom.atom_id for atom in atoms if atom.recommendation_candidate)
            coverage = RecommendationCoverageStatus.COVERED
        else:
            coverage = RecommendationCoverageStatus.NOT_CANDIDATE
        records.append(
            _audit(
                run_id=run_id,
                document=document,
                batch_id=batch.batch_id,
                atoms=atoms,
                decision=AuditDecision.ACCEPTED,
                reason_code="VALID_CHUNK",
                decision_reason=decision.decision_reason,
                candidate_type=decision.type,
                final_type=decision.type,
                chunk_id=chunk.chunk_id,
                recommendation_coverage_status=coverage,
                attempt_count=1,
            )
        )
    for decision in response.excluded_ranges:
        atoms = _range_atoms(document, decision.start_atom_id, decision.end_atom_id)
        is_candidate = any(atom.recommendation_candidate for atom in atoms)
        if is_candidate:
            covered.update(atom.atom_id for atom in atoms if atom.recommendation_candidate)
        records.append(
            _audit(
                run_id=run_id,
                document=document,
                batch_id=batch.batch_id,
                atoms=atoms,
                decision=AuditDecision.EXCLUDED,
                reason_code=decision.reason_code,
                decision_reason=decision.decision_reason,
                recommendation_coverage_status=(
                    RecommendationCoverageStatus.NOT_IN_SCOPE
                    if is_candidate
                    else RecommendationCoverageStatus.NOT_CANDIDATE
                ),
                attempt_count=1,
            )
        )
    for decision in response.review_ranges:
        atoms = _range_atoms(document, decision.start_atom_id, decision.end_atom_id)
        records.append(
            _audit(
                run_id=run_id,
                document=document,
                batch_id=batch.batch_id,
                atoms=atoms,
                decision=AuditDecision.NEEDS_REVIEW,
                reason_code=decision.reason_code,
                decision_reason=decision.decision_reason,
                attempt_count=1,
            )
        )
    return records, disposed, covered


def _state_payload(
    document: SourceDocument,
    output_dir: Path,
    cache_root: Path,
    config: PipelineConfig,
    taxonomy: TopicTaxonomy,
    batches: Sequence[SemanticBatch],
) -> dict[str, Any]:
    if config.config_path is None:
        raise ValueError("resume state requires a loaded configuration file")
    return {
        "schema_version": 1,
        "input_path": str(document.path.resolve()),
        "output_dir": str(output_dir.resolve()),
        "config_path": str(config.config_path),
        "config_sha256": sha256_file(config.config_path),
        "taxonomy_path": str(config.taxonomy.path.resolve()),
        "taxonomy_sha256": sha256_file(config.taxonomy.path),
        "source_sha256": sha256_file(document.path),
        "cache_root": str(cache_root.resolve()),
        "batch_cache_keys": {
            batch.batch_id: batch.cache_key for batch in batches if batch.cache_key
        },
    }


def _write_state(
    document: SourceDocument,
    output_dir: Path,
    cache_root: Path,
    config: PipelineConfig,
    taxonomy: TopicTaxonomy,
    batches: Sequence[SemanticBatch],
) -> Path:
    path = output_dir / STATE_NAME
    atomic_write_json(path, _state_payload(document, output_dir, cache_root, config, taxonomy, batches))
    return path


def _load_state(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError(f"invalid pipeline resume state: {path}")
    required = {
        "input_path", "output_dir", "config_path", "config_sha256", "taxonomy_path",
        "taxonomy_sha256", "source_sha256", "cache_root", "batch_cache_keys",
    }
    if not required.issubset(value):
        raise ValueError(f"incomplete pipeline resume state: {path}")
    return value


def _active_audits(records: Collection[AuditRecord]) -> list[AuditRecord]:
    return [
        record
        for record in records
        if record.decision
        in {
            AuditDecision.NEEDS_REVIEW,
            AuditDecision.LLM_FAILED,
            AuditDecision.VALIDATION_FAILED,
        }
    ]


def _make_batch_validator(
    document: SourceDocument,
    taxonomy: TopicTaxonomy,
    config: PipelineConfig,
):
    atom_index = {atom.atom_id: atom for atom in document.atoms}

    def validate(batch: SemanticBatch, response: SemanticBatchResponse) -> list[ValidationIssue]:
        return validate_semantic_response(
            batch,
            response,
            atom_index,
            taxonomy.allowed_tags,
            summary_max_chars=config.summary.max_chars,
            review_max_chars=config.chunking.review_max_chars,
        )

    return validate


def _safe_partial_response(
    document: SourceDocument,
    batch: SemanticBatch,
    response: SemanticBatchResponse,
    validator: Any,
) -> SemanticBatchResponse | None:
    """仅保留无冲突且能独立通过完整校验的知识块，其余原子转人工审核。"""

    atom_index = {atom.atom_id: atom for atom in document.atoms}
    positions = {atom.atom_id: index for index, atom in enumerate(document.atoms)}
    core_ids = set(batch.core_atom_ids)

    def decision_atom_ids(
        decision: SemanticChunkDecision | SemanticRangeDecision,
    ) -> list[str] | None:
        if (
            decision.start_atom_id not in positions
            or decision.end_atom_id not in positions
        ):
            return None
        start = positions[decision.start_atom_id]
        end = positions[decision.end_atom_id]
        if start > end:
            return None
        atoms = document.atoms[start : end + 1]
        ids = [atom.atom_id for atom in atoms]
        if not ids or any(atom_id not in core_ids for atom_id in ids):
            return None
        if len({(atom.section_id, tuple(atom.section_path)) for atom in atoms}) != 1:
            return None
        return ids

    disposition_counts: Counter[str] = Counter()
    groups = (response.chunks, response.excluded_ranges, response.review_ranges)
    for group in groups:
        for decision in group:
            ids = decision_atom_ids(decision)
            if ids is not None:
                disposition_counts.update(ids)

    accepted: list[SemanticChunkDecision] = []
    claimed: set[str] = set()
    ordered_chunks = sorted(
        response.chunks,
        key=lambda decision: positions.get(decision.start_atom_id, len(positions)),
    )
    for decision in ordered_chunks:
        ids = decision_atom_ids(decision)
        if ids is None or any(disposition_counts[atom_id] != 1 for atom_id in ids):
            continue
        sub_batch = batch.model_copy(
            update={
                "core_atom_ids": ids,
                "context_before_ids": [],
                "context_after_ids": [],
                "core_char_count": sum(len(atom_index[atom_id].text) for atom_id in ids),
                "cache_key": None,
            }
        )
        candidate = SemanticBatchResponse(chunks=[decision])
        if validator(sub_batch, candidate):
            continue
        accepted.append(decision)
        claimed.update(ids)

    if not accepted:
        return None

    review_ranges = [
        SemanticRangeDecision(
            start_atom_id=atom_id,
            end_atom_id=atom_id,
            reason_code="MODEL_OUTPUT_INVALID",
            decision_reason=(
                "final model response failed batch validation; "
                "atom was not part of a uniquely valid chunk"
            ),
        )
        for atom_id in batch.core_atom_ids
        if atom_id not in claimed
    ]
    partial = SemanticBatchResponse(chunks=accepted, review_ranges=review_ranges)
    return partial if not validator(batch, partial) else None


def run_document(
    input_path: Path,
    output_root: Path,
    config: PipelineConfig,
    semantic_client: SemanticClient | None,
    *,
    dry_run: bool = False,
    force: bool = False,
    cache_root: Path | None = None,
) -> DocumentRunResult:
    """完成单份 Markdown 指南从原文解析到正式产物发布的全部流程。

    Args:
        input_path:
            当前指南的 Markdown 文件路径。
        output_root:
            所有指南共享的输出根目录；当前指南使用文件名 stem 建立子目录。
        config:
            已加载并校验的流水线配置。
        semantic_client:
            负责语义组合的模型客户端。``dry_run=True`` 时允许为 ``None``。
        dry_run:
            只执行解析、推荐候选标记和批次规划，不请求模型。
        force:
            为命令行接口保留的参数；当前函数尚未使用它改变覆盖或缓存行为。
        cache_root:
            可选的缓存目录覆盖值，主要供 ``resume`` 复用原运行缓存。

    Returns:
        包含文档名、最终状态、输出目录和四个正式产物路径的运行结果。

    单个批次失败不会终止整本文档，而会生成审计记录并继续处理后续批次；
    函数级异常则由上层 ``run_inputs()`` 隔离到当前指南。
    """

    # ── 阶段 1：确定运行身份、时间和输出位置 ──────────────────────────
    # UTC 时间写入 manifest/report，单调时钟只用于不受系统时间调整影响的耗时日志。
    started_at = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    source_path = Path(input_path).resolve()

    # 每本指南固定写入 output_root/<Markdown文件名stem>/。
    output_dir = Path(output_root).resolve() / source_path.stem
    logger.info(
        "开始处理指南：文件=%s，输出=%s，模式=%s",
        source_path,
        output_dir,
        "预演" if dry_run else "正式运行",
    )

    # taxonomy 是模型可选择的受控主题标签白名单，加载时会校验版本、
    # 父子编码、重复项以及 14 个一级/102 个二级主题的固定契约。
    taxonomy = load_taxonomy(config.taxonomy.path)
    logger.info(
        "配置加载完成：模型=%s，单次超时=%d 秒，最多尝试=%d，缓存=%s",
        config.deepseek.model,
        config.deepseek.timeout_seconds,
        min(config.deepseek.max_attempts, 3),
        "开启" if config.cache.enabled and not dry_run else "关闭",
    )

    # ── 阶段 2：Markdown 预检、结构扫描和原子化 ───────────────────────
    # parse_source_document() 会完成 UTF-8/标题/围栏预检，将段落、列表、
    # 表格等结构转换成带绝对字符区间的 SourceAtom。
    document = parse_source_document(source_path)

    # 原子创建后再执行一次保守的推荐候选扫描，综合章节标题、引导语、
    # 规范性动词和推荐等级。model_copy() 保留原文等字段，只替换 atoms。
    document = document.model_copy(
        update={"atoms": mark_recommendation_candidates(document.atoms)}
    )
    recommendation_count = sum(
        1 for atom in document.atoms if atom.recommendation_candidate
    )
    logger.info(
        "Markdown 解析完成：原子=%d，推荐候选=%d，字符=%d",
        len(document.atoms),
        recommendation_count,
        len(document.text),
    )

    # ── 阶段 3：在章节边界内规划模型批次 ─────────────────────────────
    # 每个 atom 只属于一个批次的 core；相邻 atom 可以作为只读上下文出现。
    planned = plan_batches(document.atoms, config.batching)
    logger.info("批次规划完成：共 %d 个语义批次", len(planned))

    # ── 阶段 4：建立确定性缓存键和断点状态 ───────────────────────────
    # 缓存键不仅依赖源文件，还覆盖 atom、批次、配置、标签体系、模型和提示版本。
    source_hash = sha256_file(document.path)

    # 正常运行使用配置中的缓存目录；resume 可以显式传回上次的缓存根目录。
    cache_path = Path(cache_root) if cache_root is not None else _cache_root(Path(output_root), config)

    # dry-run 强制关闭缓存，确保预演既不读取旧语义结果，也不产生新缓存。
    cache = CacheStore(cache_path, enabled=config.cache.enabled and not dry_run)
    batches: list[SemanticBatch] = []
    for batch in planned:
        # 缓存输入同时包含核心 atom 和前后只读上下文 atom。
        atoms = _batch_atoms(document, batch)
        key = build_cache_key(source_hash, batch, atoms, config, taxonomy)

        # SemanticBatch 是 Pydantic 模型；复制后附加该批次的内容寻址缓存键。
        batches.append(batch.model_copy(update={"cache_key": key}))

    # 状态文件保存输入、配置和标签文件哈希及缓存位置。
    # resume 会先核对这些身份信息，再重新进入本函数并依靠缓存跳过已完成批次。
    _write_state(document, output_dir, cache_path, config, taxonomy, batches)
    logger.info("断点状态已写入：%s", output_dir / STATE_NAME)

    # ── 阶段 5：初始化文档级累计状态 ─────────────────────────────────
    # run_id 关联 manifest、processing report 和每一条 AuditRecord。
    run_id = f"RUN_{uuid4().hex.upper()}"

    # chunks 保存已组装的正式知识块；spans 保存 chunk_id 到原文字符范围的映射。
    chunks: list[Chunk] = []
    spans: dict[str, tuple[int, int]] = {}

    # records 记录每个原文范围为何接受、排除、失败或需要人工审核。
    records: list[AuditRecord] = []

    # disposition_ids 用于证明每个源 atom 已有处置；
    # covered_recommendations 用于证明推荐候选已被知识块或排除决定覆盖。
    disposition_ids: set[str] = set()
    covered_recommendations: set[str] = set()

    # 批次与文档校验问题最终汇总进 processing_report。
    validation_issues: list[ValidationIssue] = []

    # 这些计数只用于报告 DeepSeek 调用、缓存和 token 使用情况。
    requests = retries = cache_hits = cache_misses = prompt_tokens = completion_tokens = 0

    # ── 阶段 6：提前隔离不能安全放入普通批次的超长原子 ──────────────
    # 超长表格或推荐原子不可继续按规则拆分，因此不发送给模型，直接转人工审核。
    oversized_atoms = find_oversized_atoms(document.atoms, config.batching)
    if oversized_atoms:
        logger.warning(
            "发现 %d 个超长原子，将进入人工审核而不发送给模型",
            len(oversized_atoms),
        )
    for atom in oversized_atoms:
        records.append(
            _audit(
                run_id=run_id,
                document=document,
                batch_id=None,
                atoms=[atom],
                decision=AuditDecision.NEEDS_REVIEW,
                reason_code="OVERSIZED_ATOM",
                decision_reason="indivisible source atom exceeds the semantic batch limit",
            )
        )

        # 虽然没有形成 chunk，但该 atom 已有明确的 NEEDS_REVIEW 处置。
        disposition_ids.add(atom.atom_id)

    # ── 阶段 7：预演分支或正式语义处理 ───────────────────────────────
    if dry_run:
        # 预演只验证确定性前处理能够完成，不创建模型请求和正式知识块。
        logger.info("预演模式：跳过全部 DeepSeek 请求")
        status = RunStatus.DRY_RUN_COMPLETED
    else:
        if semantic_client is None:
            raise ValueError("semantic_client is required unless dry_run is enabled")

        # 注册表在整篇文档范围内按原文顺序签发稳定 chunk_id，并处理理论哈希碰撞。
        registry = DocumentChunkIdRegistry(document.source_doc)

        # validator 将当前文档、标签白名单和长度配置闭包到批次校验函数中。
        validator = _make_batch_validator(document, taxonomy, config)

        # 批次按原文顺序串行处理，从而保证 chunk 顺序和稳定 ID 不受并发完成顺序影响。
        for batch_number, batch in enumerate(batches, start=1):
            # 提示词需要核心 atom 和前后只读上下文的完整 SourceAtom 数据。
            batch_atoms = _batch_atoms(document, batch)
            logger.info(
                "开始批次 %d/%d：批次=%s，核心原子=%d，上下文原子=%d",
                batch_number,
                len(batches),
                batch.batch_id,
                len(batch.core_atom_ids),
                len(batch_atoms) - len(batch.core_atom_ids),
            )

            # ── 阶段 7.1：优先复用缓存 ───────────────────────────────
            cached = cache.get(batch.cache_key or "")
            if cached is not None:
                try:
                    # 即便 CacheStore 已校验磁盘结构，也用当前响应模型再次解析，
                    # 防止旧版本缓存不符合当前 SemanticBatchResponse 契约。
                    response = SemanticBatchResponse.model_validate(
                        cached["parsed_response"]
                    )
                except ValidationError as error:
                    # 损坏或过期缓存不终止流水线，只退化为一次正常模型请求。
                    cached = None
                    logger.warning(
                        "缓存中的语义响应不符合当前契约，按未命中处理："
                        "批次=%s，校验错误=%d",
                        batch.batch_id,
                        error.error_count(),
                    )
                else:
                    cache_hits += 1
                    logger.info(
                        "缓存命中：批次=%s，跳过 DeepSeek 请求",
                        batch.batch_id,
                    )

            if cached is None:
                # ── 阶段 7.2：缓存未命中时请求 DeepSeek ──────────────
                cache_misses += 1
                logger.info("缓存未命中：批次=%s", batch.batch_id)
                call_started = datetime.now(timezone.utc)

                # salvaged_response=True 表示完整响应失败，但安全保留了局部有效块；
                # 这种部分结果不能写缓存，避免将降级结果当作完整模型成功结果复用。
                salvaged_response = False
                try:
                    # 模型返回的是 atom 范围、类型、标签、摘要和处置理由。
                    # analyze_with_retry() 会在每次响应后立即执行 validator，
                    # 对可重试的传输/结构/语义错误最多尝试三次。
                    response = analyze_with_retry(
                        semantic_client,
                        batch,
                        validator,
                        max_attempts=config.deepseek.max_attempts,
                        atoms=batch_atoms,
                    )
                except Exception as error:
                    # 重试耗尽错误携带真实尝试次数及最后一份结构合法响应。
                    attempts = error.attempts if isinstance(error, RetryExhaustedError) else 1

                    # 若最后响应只是局部有问题，尝试保留“范围唯一且能独立通过
                    # 完整校验”的 chunk，其余核心 atom 统一转入 review_ranges。
                    partial = (
                        _safe_partial_response(
                            document,
                            batch,
                            error.last_response,
                            validator,
                        )
                        if isinstance(error, RetryExhaustedError)
                        and error.last_response is not None
                        else None
                    )
                    if partial is not None:
                        # 局部保留仍然记录所有真实请求/重试，但不会写入缓存。
                        response = partial
                        salvaged_response = True
                        requests += attempts
                        retries += max(0, attempts - 1)
                        logger.warning(
                            "批次完整响应重试耗尽，但已安全保留局部结果："
                            "批次=%s，知识块=%d，转人工审核原子=%d；不写入缓存",
                            batch.batch_id,
                            len(partial.chunks),
                            len(partial.review_ranges),
                        )
                    else:
                        # 无法安全保留时，不丢弃原子：为该批次每个核心 atom
                        # 生成失败审计记录，然后继续下一批。
                        logger.error(
                            "批次语义分析失败：批次=%s，尝试=%d，错误类型=%s；"
                            "该批次将进入审核，继续处理后续批次",
                            batch.batch_id,
                            attempts,
                            type(error).__name__,
                        )

                        # AssemblyError 属于本地结果校验失败；其他异常在此记为
                        # 模型/请求失败。二者会产生不同的审计 decision。
                        decision = (
                            AuditDecision.VALIDATION_FAILED
                            if isinstance(error, AssemblyError)
                            else AuditDecision.LLM_FAILED
                        )

                        # 优先保留重试器聚合的结构化校验问题，便于人工定位；
                        # 普通异常则至少记录经过上层脱敏处理的错误描述。
                        validation_errors = (
                            error.validation_errors
                            if isinstance(error, RetryExhaustedError)
                            and error.validation_errors
                            else [str(error)]
                        )
                        for atom_id in batch.core_atom_ids:
                            # 失败审计按单 atom 记录，确保覆盖率能够精确定位。
                            atom = next(
                                atom
                                for atom in document.atoms
                                if atom.atom_id == atom_id
                            )
                            records.append(
                                _audit(
                                    run_id=run_id,
                                    document=document,
                                    batch_id=batch.batch_id,
                                    atoms=[atom],
                                    decision=decision,
                                    reason_code=(
                                        "SEMANTIC_VALIDATION_FAILED"
                                        if decision is AuditDecision.VALIDATION_FAILED
                                        else "SEMANTIC_REQUEST_FAILED"
                                    ),
                                    decision_reason=str(error),
                                    attempt_count=attempts,
                                    validation_errors=validation_errors,
                                )
                            )
                            disposition_ids.add(atom_id)

                        requests += attempts
                        retries += max(0, attempts - 1)
                        continue

                call_completed = datetime.now(timezone.utc)
                if not salvaged_response:
                    # 完整成功响应才会统计供应商 token 并写入长期缓存。
                    requests += 1
                    exchange = getattr(semantic_client, "last_exchange", None) or {}
                    usage = (
                        exchange.get("token_usage")
                        if isinstance(exchange, dict)
                        else None
                    )
                    if isinstance(usage, dict):
                        prompt_tokens += int(usage.get("prompt_tokens", 0))
                        completion_tokens += int(usage.get("completion_tokens", 0))

                    # 缓存内容在 build_cache_entry()/CacheStore.put() 中进行
                    # 严格建模、敏感信息清理和临时文件原子替换。
                    cache.put(
                        batch.cache_key or "",
                        build_cache_entry(
                            raw_response=(
                                exchange.get("raw_response")
                                if isinstance(exchange, dict)
                                else None
                            ),
                            parsed_response=response,
                            attempts=1,
                            validation_errors=[],
                            started_at=call_started,
                            completed_at=call_completed,
                            elapsed_seconds=(
                                call_completed - call_started
                            ).total_seconds(),
                            token_usage=usage if isinstance(usage, dict) else None,
                        ),
                    )
                    logger.info(
                        "批次响应已缓存：批次=%s，API 耗时=%.1f 秒",
                        batch.batch_id,
                        (call_completed - call_started).total_seconds(),
                    )

            # ── 阶段 7.3：把模型范围决定组装为正式知识块 ─────────────
            try:
                # assemble_batch() 会再次校验响应，然后依据 start/end atom 的
                # 字符区间从 document.text 回切 content；模型不能生成正式正文。
                assembled = assemble_batch(
                    document,
                    batch,
                    response,
                    taxonomy=taxonomy,
                    id_registry=registry,
                    summary_max_chars=config.summary.max_chars,
                    review_max_chars=config.chunking.review_max_chars,
                )
            except AssemblyError as error:
                # 组装阶段若仍发现问题，整批不进入 chunks，而是转为逐 atom
                # VALIDATION_FAILED 审计，避免发布未经验证的部分结果。
                logger.warning(
                    "批次组装校验失败：批次=%s，问题=%d；该批次将进入人工审核",
                    batch.batch_id,
                    len(error.issues),
                )
                validation_issues.extend(error.issues)
                for atom_id in batch.core_atom_ids:
                    atom = next(atom for atom in document.atoms if atom.atom_id == atom_id)
                    records.append(
                        _audit(
                            run_id=run_id,
                            document=document,
                            batch_id=batch.batch_id,
                            atoms=[atom],
                            decision=AuditDecision.VALIDATION_FAILED,
                            reason_code="SEMANTIC_VALIDATION_FAILED",
                            decision_reason=str(error),
                            attempt_count=1,
                            validation_errors=[str(issue) for issue in error.issues],
                        )
                    )
                    disposition_ids.add(atom_id)
                continue

            # 正式块和原文 span 都按批次追加到文档级集合。
            chunks.extend(assembled.chunks)
            spans.update(assembled.chunk_spans)

            # 把模型的 chunks/excluded_ranges/review_ranges 全部转换成持久化审计，
            # 同时返回本批已处置 atom 和已覆盖推荐候选的证据集合。
            audit_records, disposed, covered = _response_audits(
                run_id=run_id,
                document=document,
                batch=batch,
                response=response,
                chunks=assembled.chunks,
            )
            records.extend(audit_records)
            disposition_ids.update(disposed)
            covered_recommendations.update(covered)
            logger.info(
                "批次完成 %d/%d：批次=%s，新增知识块=%d，累计知识块=%d",
                batch_number,
                len(batches),
                batch.batch_id,
                len(assembled.chunks),
                len(chunks),
            )

        # ── 阶段 8：执行整篇文档的全局质量校验 ───────────────────────
        # 批次级校验看不到跨批次问题，因此这里重新检查正式块顺序、重叠、
        # 原文精确性、稳定 ID、全 atom 处置率、推荐覆盖率和未解决审计。
        logger.info("开始文档级质量校验：知识块=%d，审计记录=%d", len(chunks), len(records))
        validation_issues.extend(
            validate_document_chunks(
                document,
                chunks,
                spans,
                allowed_tags=taxonomy.allowed_tags,
                disposition_atom_ids=disposition_ids,
                recommendation_covered_atom_ids=covered_recommendations,
                unresolved_audit_records=_active_audits(records),
                summary_max_chars=config.summary.max_chars,
                review_max_chars=config.chunking.review_max_chars,
            )
        )
        logger.info("文档级质量校验完成：问题=%d", len(validation_issues))
        status = RunStatus.COMPLETED

    # ── 阶段 9：汇总统计、计算质量门并确定最终状态 ───────────────────
    completed_at = datetime.now(timezone.utc)
    statistics = DeepSeekStatistics(
        requests=requests,
        retries=retries,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    report = build_processing_report(
        document,
        chunks,
        records,
        run_id=run_id,
        status=status,
        validation_issues=validation_issues,
        disposition_atom_ids=disposition_ids,
        recommendation_covered_atom_ids=covered_recommendations,
        deepseek=statistics,
        started_at=started_at,
        completed_at=completed_at,
    )

    # “代码执行完成”不等于“质量门通过”。只要覆盖率不足、存在待审核项或
    # 未解决失败，状态就降为 completed_with_review，而不是伪装成 completed。
    if status is RunStatus.COMPLETED and not report.quality_gate_passed:
        status = RunStatus.COMPLETED_WITH_REVIEW
        report = report.model_copy(update={"status": status})

    # manifest 保存输入/配置/标签哈希、版本、运行身份和正式产物计数，
    # 用于以后复现或核对这一批结果。
    manifest = build_manifest(
        document,
        run_id=run_id,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        configuration=build_configuration_identity(config, taxonomy),
        chunks=chunks,
        audit_records=records,
    )

    # ── 阶段 10：交叉验证并事务式发布四项正式产物 ────────────────────
    # write_document_outputs() 会先验证 chunks、manifest、audit、report
    # 彼此一致，再统一替换目标文件；失败时回滚，避免留下半套输出。
    paths = write_document_outputs(output_dir, chunks, manifest, records, report)
    logger.info(
        "指南处理完成：文件=%s，状态=%s，知识块=%d，审计记录=%d，"
        "缓存命中=%d，缓存未命中=%d，总耗时=%.1f 秒，输出=%s",
        source_path.name,
        status.value,
        len(chunks),
        len(records),
        cache_hits,
        cache_misses,
        time.monotonic() - started_clock,
        output_dir,
    )

    # 返回轻量结果给 run_inputs()/CLI；详细数据已经写入四个正式文件。
    return DocumentRunResult(document.source_doc, status, output_dir, paths)


def run_inputs(
    input_path: Path,
    output_root: Path,
    config: PipelineConfig,
    semantic_client: SemanticClient | None,
    *,
    pattern: str = "*.md",
    recursive: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> BatchRunResult:
    """独立运行每份已发现的指南；单份异常指南不会阻塞其他指南。"""

    # 获取目录下的所有md文件，按字母顺序排序，保证输出目录名称不冲突
    guides = discover_inputs(input_path, pattern=pattern, recursive=recursive)
    logger.info(
        "输入发现完成：路径=%s，指南数量=%d，匹配模式=%s，递归=%s",
        Path(input_path).resolve(),
        len(guides),
        pattern,
        "是" if recursive else "否",
    )
    root = Path(output_root).resolve()
    source = Path(input_path).resolve()
    if source.is_dir() and (root == source or source in root.parents):
        raise InputConflictError("output directory must not be inside the input directory")
    results: list[DocumentRunResult] = []
    for guide_number, guide in enumerate(guides, start=1):
        logger.info(
            "文档进度 %d/%d：%s",
            guide_number,
            len(guides),
            guide.name,
        )
        try:
            results.append(
                run_document(
                    guide,
                    root,
                    config,
                    semantic_client,
                    dry_run=dry_run,
                    force=force,
                )
            )
        except Exception as error:
            logger.error(
                "指南处理失败：文件=%s，错误类型=%s",
                guide,
                type(error).__name__,
            )
            results.append(
                DocumentRunResult(
                    source_doc=guide.stem,
                    status=RunStatus.FAILED,
                    output_dir=root / guide.stem,
                    error=str(error),
                )
            )
    return BatchRunResult(results)


def resume_outputs(output_path: Path, semantic_client: SemanticClient) -> BatchRunResult:
    """验证已持久化的标识，并仅重新运行缓存缺失的语义批次。"""

    root = Path(output_path).resolve()
    states = [root / STATE_NAME] if (root / STATE_NAME).is_file() else sorted(root.glob(f"*/{STATE_NAME}"))
    if not states:
        raise FileNotFoundError(f"no {STATE_NAME} found below {root}")
    results: list[DocumentRunResult] = []
    for state_path in states:
        state = _load_state(state_path)
        config_path = Path(state["config_path"])
        taxonomy_path = Path(state["taxonomy_path"])
        source_path = Path(state["input_path"])
        if sha256_file(config_path) != state["config_sha256"]:
            raise ValueError("resume config hash mismatch")
        if sha256_file(taxonomy_path) != state["taxonomy_sha256"]:
            raise ValueError("resume taxonomy hash mismatch")
        if sha256_file(source_path) != state["source_sha256"]:
            raise ValueError("resume source hash mismatch")
        config = load_pipeline_config(config_path)
        results.append(
            run_document(
                source_path,
                Path(state["output_dir"]).parent,
                config,
                semantic_client,
                cache_root=Path(state["cache_root"]),
            )
        )
    return BatchRunResult(results)
