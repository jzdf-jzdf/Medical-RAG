"""确定性的清单与处理报告聚合。"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import __version__
from .config import PipelineConfig
from .models import (
    AuditDecision,
    AuditRecord,
    FactChunk,
    RecommendationChunk,
    RunStatus,
    SourceDocument,
)
from .prompts import PROMPT_VERSION, RESPONSE_SCHEMA_VERSION
from .taxonomy import TopicTaxonomy
from .validation import ValidationIssue


RULES_VERSION = "deterministic_rules_v1"
SCHEMA_VERSION = "1.0"

Chunk = FactChunk | RecommendationChunk


class StrictReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigurationIdentity(StrictReportModel):
    config_sha256: str
    taxonomy_version: str
    taxonomy_sha256: str
    rules_version: str
    prompt_version: str
    response_schema_version: str
    model: str


class ManifestCounts(StrictReportModel):
    chunks: int = Field(ge=0)
    fact: int = Field(ge=0)
    recommendation: int = Field(ge=0)
    audit_records: int = Field(ge=0)


class OutputNames(StrictReportModel):
    chunks: str = "chunks.jsonl"
    audit: str = "audit.jsonl"
    processing_report: str = "processing_report.json"


class Manifest(StrictReportModel):
    schema_version: str
    pipeline_version: str
    run_id: str
    status: RunStatus
    source_doc: str
    input_filename: str
    input_stem: str
    input_sha256: str
    started_at: datetime
    completed_at: datetime
    configuration: ConfigurationIdentity
    counts: ManifestCounts
    outputs: OutputNames

    @model_validator(mode="after")
    def validate_manifest(self) -> "Manifest":
        _validate_timing(self.started_at, self.completed_at)
        if self.counts.chunks != self.counts.fact + self.counts.recommendation:
            raise ValueError("manifest chunk counts are inconsistent")
        if self.outputs != OutputNames():
            raise ValueError("manifest output names must use the four-file contract")
        return self


class SourceStatistics(StrictReportModel):
    characters: int = Field(ge=0)
    sections: int = Field(ge=0)
    atoms: int = Field(ge=0)
    recommendation_candidates: int = Field(ge=0)


class DecisionStatistics(StrictReportModel):
    accepted: int = Field(ge=0)
    excluded: int = Field(ge=0)
    needs_review: int = Field(ge=0)
    llm_failed: int = Field(ge=0)
    validation_failed: int = Field(ge=0)
    manually_resolved: int = Field(ge=0)


class ChunkStatistics(StrictReportModel):
    total: int = Field(ge=0)
    fact: int = Field(ge=0)
    recommendation: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> "ChunkStatistics":
        if self.total != self.fact + self.recommendation:
            raise ValueError("processing report chunk counts are inconsistent")
        return self


class AtomDispositionStatistics(StrictReportModel):
    total: int = Field(ge=0)
    disposed: int = Field(ge=0)
    undisposed: int = Field(ge=0)
    rate: float = Field(ge=0.0, le=1.0)


class RecommendationCoverageStatistics(StrictReportModel):
    candidates: int = Field(ge=0)
    covered: int = Field(ge=0)
    uncovered: int = Field(ge=0)
    rate: float = Field(ge=0.0, le=1.0)


class DeepSeekStatistics(StrictReportModel):
    requests: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    cache_misses: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_tokens(self) -> "DeepSeekStatistics":
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total_tokens must equal prompt_tokens + completion_tokens")
        return self


class TimingStatistics(StrictReportModel):
    started_at: datetime
    completed_at: datetime
    elapsed_seconds: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_timing(self) -> "TimingStatistics":
        _validate_timing(self.started_at, self.completed_at)
        return self


class QualityMetrics(StrictReportModel):
    schema_valid_rate: float = Field(ge=0.0, le=1.0)
    exact_source_match_rate: float = Field(ge=0.0, le=1.0)
    continuous_source_rate: float = Field(ge=0.0, le=1.0)
    non_overlap_rate: float = Field(ge=0.0, le=1.0)
    valid_topic_tag_rate: float = Field(ge=0.0, le=1.0)
    summary_valid_rate: float = Field(ge=0.0, le=1.0)
    source_disposition_rate: float = Field(ge=0.0, le=1.0)
    recommendation_coverage_rate: float = Field(ge=0.0, le=1.0)
    stable_id_valid_rate: float = Field(ge=0.0, le=1.0)
    duplicate_chunk_ids: int = Field(ge=0)
    pending_review_count: int = Field(ge=0)
    unresolved_failure_count: int = Field(ge=0)


class ProcessingReport(StrictReportModel):
    schema_version: str
    run_id: str
    status: RunStatus
    source_doc: str
    source: SourceStatistics
    decisions: DecisionStatistics
    chunks: ChunkStatistics
    atom_disposition: AtomDispositionStatistics
    recommendation_coverage: RecommendationCoverageStatistics
    quality_metrics: QualityMetrics
    topic_distribution: dict[str, int]
    deepseek: DeepSeekStatistics
    timing: TimingStatistics
    pending_audit_ids: list[str]
    quality_gate_passed: bool


def _validate_timing(started_at: datetime, completed_at: datetime) -> None:
    if any(value.tzinfo is None or value.utcoffset() is None for value in (started_at, completed_at)):
        raise ValueError("timestamps must include a timezone")
    if completed_at < started_at:
        raise ValueError("completed_at must not precede started_at")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_configuration_identity(
    config: PipelineConfig,
    taxonomy: TopicTaxonomy,
    *,
    rules_version: str = RULES_VERSION,
) -> ConfigurationIdentity:
    if config.config_path is None:
        raise ValueError("configuration identity requires a loaded config file")
    return ConfigurationIdentity(
        config_sha256=sha256_file(config.config_path),
        taxonomy_version=taxonomy.version,
        taxonomy_sha256=sha256_file(config.taxonomy.path),
        rules_version=rules_version,
        prompt_version=PROMPT_VERSION,
        response_schema_version=RESPONSE_SCHEMA_VERSION,
        model=config.deepseek.model,
    )


def _chunk_counts(chunks: Sequence[Chunk]) -> tuple[int, int]:
    facts = sum(isinstance(chunk, FactChunk) for chunk in chunks)
    recommendations = sum(isinstance(chunk, RecommendationChunk) for chunk in chunks)
    return facts, recommendations


def build_manifest(
    document: SourceDocument,
    *,
    run_id: str,
    status: RunStatus | str,
    started_at: datetime,
    configuration: ConfigurationIdentity | Mapping[str, str],
    chunks: Sequence[Chunk],
    audit_records: Sequence[AuditRecord],
    completed_at: datetime | None = None,
) -> Manifest:
    finished = completed_at or datetime.now(timezone.utc)
    facts, recommendations = _chunk_counts(chunks)
    return Manifest(
        schema_version=SCHEMA_VERSION,
        pipeline_version=__version__,
        run_id=run_id,
        status=status,
        source_doc=document.source_doc,
        input_filename=document.path.name,
        input_stem=document.path.stem,
        input_sha256=sha256_file(document.path),
        started_at=started_at,
        completed_at=finished,
        configuration=configuration,
        counts=ManifestCounts(
            chunks=len(chunks),
            fact=facts,
            recommendation=recommendations,
            audit_records=len(audit_records),
        ),
        outputs=OutputNames(),
    )


def _rate(total: int, invalid: int) -> float:
    if total == 0:
        return 1.0
    return max(0.0, (total - min(total, invalid)) / total)


def _count_invalid_units(
    issues: Sequence[ValidationIssue],
    codes: Collection[str] | None = None,
    *,
    code_prefix: str | None = None,
    extra_codes: Collection[str] = (),
) -> int:
    invalid_units: set[str] = set()
    for index, issue in enumerate(issues):
        matched = (
            codes is not None and issue.code in codes
        ) or (
            code_prefix is not None and issue.code.startswith(code_prefix)
        ) or issue.code in extra_codes
        if not matched:
            continue
        if issue.atom_id:
            key = f"atom:{issue.atom_id}"
        elif issue.detail:
            key = f"detail:{issue.detail.split(':', 1)[0].strip()}"
        else:
            key = f"issue:{index}"
        invalid_units.add(key)
    return len(invalid_units)


def _active_audits(records: Sequence[AuditRecord]) -> list[AuditRecord]:
    resolved = {
        record.parent_audit_id
        for record in records
        if record.decision is AuditDecision.MANUALLY_RESOLVED
    }
    pending = {
        AuditDecision.NEEDS_REVIEW,
        AuditDecision.LLM_FAILED,
        AuditDecision.VALIDATION_FAILED,
    }
    return [
        record
        for record in records
        if record.decision in pending and record.audit_id not in resolved
    ]


def compute_quality_gate(metrics: QualityMetrics) -> bool:
    rates = (
        metrics.schema_valid_rate,
        metrics.exact_source_match_rate,
        metrics.continuous_source_rate,
        metrics.non_overlap_rate,
        metrics.valid_topic_tag_rate,
        metrics.source_disposition_rate,
        metrics.recommendation_coverage_rate,
        metrics.stable_id_valid_rate,
    )
    return (
        all(rate == 1.0 for rate in rates)
        and metrics.duplicate_chunk_ids == 0
        and metrics.pending_review_count == 0
        and metrics.unresolved_failure_count == 0
    )


def build_processing_report(
    document: SourceDocument,
    chunks: Sequence[Chunk],
    audit_records: Sequence[AuditRecord],
    *,
    run_id: str,
    status: RunStatus | str,
    validation_issues: Sequence[ValidationIssue],
    disposition_atom_ids: Collection[str],
    recommendation_covered_atom_ids: Collection[str],
    deepseek: DeepSeekStatistics,
    started_at: datetime,
    completed_at: datetime | None = None,
) -> ProcessingReport:
    finished = completed_at or datetime.now(timezone.utc)
    _validate_timing(started_at, finished)
    facts, recommendations = _chunk_counts(chunks)
    decisions = Counter(record.decision.value for record in audit_records)
    atom_ids = {atom.atom_id for atom in document.atoms}
    disposed = len(atom_ids.intersection(disposition_atom_ids))
    candidate_ids = {
        atom.atom_id for atom in document.atoms if atom.recommendation_candidate
    }
    covered = len(candidate_ids.intersection(recommendation_covered_atom_ids))
    active = _active_audits(audit_records)
    pending_review = sum(
        record.decision is AuditDecision.NEEDS_REVIEW for record in active
    )
    unresolved_failures = sum(
        record.decision
        in {AuditDecision.LLM_FAILED, AuditDecision.VALIDATION_FAILED}
        for record in active
    )
    total_chunks = len(chunks)
    duplicate_ids = total_chunks - len({chunk.chunk_id for chunk in chunks})
    metrics = QualityMetrics(
        schema_valid_rate=_rate(
            total_chunks,
            _count_invalid_units(validation_issues, {"INVALID_CHUNK_FIELDS", "INVALID_CHUNK_TYPE"}),
        ),
        exact_source_match_rate=_rate(
            total_chunks,
            _count_invalid_units(validation_issues, {"NON_EXACT_SOURCE", "SOURCE_DOC_MISMATCH"}),
        ),
        continuous_source_rate=_rate(
            total_chunks,
            _count_invalid_units(
                validation_issues,
                {"MISSING_CHUNK_SPAN", "INVALID_CHUNK_SPAN", "NON_ATOM_BOUNDARY", "CHUNK_SECTION_MISMATCH"},
            ),
        ),
        non_overlap_rate=_rate(
            total_chunks,
            _count_invalid_units(validation_issues, {"OVERLAPPING_CHUNK", "CHUNKS_OUT_OF_SOURCE_ORDER"}),
        ),
        valid_topic_tag_rate=_rate(
            total_chunks,
            _count_invalid_units(validation_issues, {"INVALID_TOPIC_COUNT", "DUPLICATE_TOPIC_TAG", "INVALID_TOPIC_TAG"}),
        ),
        summary_valid_rate=_rate(
            total_chunks,
            _count_invalid_units(
                validation_issues,
                code_prefix="SUMMARY_",
                extra_codes={"INVALID_RECOMMENDATION_LEVELS", "FACT_CONTAINS_RECOMMENDATION_LEVEL"},
            ),
        ),
        source_disposition_rate=_rate(len(atom_ids), len(atom_ids) - disposed),
        recommendation_coverage_rate=_rate(len(candidate_ids), len(candidate_ids) - covered),
        stable_id_valid_rate=_rate(
            total_chunks,
            _count_invalid_units(validation_issues, {"INVALID_STABLE_ID"}),
        ),
        duplicate_chunk_ids=duplicate_ids,
        pending_review_count=pending_review,
        unresolved_failure_count=unresolved_failures,
    )
    topic_distribution = dict(
        sorted(Counter(tag for chunk in chunks for tag in chunk.topic_tags).items())
    )
    report = ProcessingReport(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        status=status,
        source_doc=document.source_doc,
        source=SourceStatistics(
            characters=len(document.text),
            sections=len({atom.section_id for atom in document.atoms}),
            atoms=len(document.atoms),
            recommendation_candidates=len(candidate_ids),
        ),
        decisions=DecisionStatistics(
            **{decision.value: decisions[decision.value] for decision in AuditDecision}
        ),
        chunks=ChunkStatistics(
            total=total_chunks,
            fact=facts,
            recommendation=recommendations,
        ),
        atom_disposition=AtomDispositionStatistics(
            total=len(atom_ids),
            disposed=disposed,
            undisposed=len(atom_ids) - disposed,
            rate=_rate(len(atom_ids), len(atom_ids) - disposed),
        ),
        recommendation_coverage=RecommendationCoverageStatistics(
            candidates=len(candidate_ids),
            covered=covered,
            uncovered=len(candidate_ids) - covered,
            rate=_rate(len(candidate_ids), len(candidate_ids) - covered),
        ),
        quality_metrics=metrics,
        topic_distribution=topic_distribution,
        deepseek=deepseek,
        timing=TimingStatistics(
            started_at=started_at,
            completed_at=finished,
            elapsed_seconds=(finished - started_at).total_seconds(),
        ),
        pending_audit_ids=sorted(record.audit_id for record in active),
        quality_gate_passed=compute_quality_gate(metrics),
    )
    return report
