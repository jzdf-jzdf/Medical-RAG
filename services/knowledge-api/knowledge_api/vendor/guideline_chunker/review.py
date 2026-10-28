"""以仅追加方式人工处理经过审计的精确原文范围。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, model_validator

from .assembly import DocumentChunkIdRegistry, assemble_semantic_chunk
from .config import load_pipeline_config
from .markdown import parse_source_document
from .models import (
    AuditDecision,
    AuditRecord,
    ChunkType,
    RecommendationCoverageStatus,
    RunStatus,
    SemanticChunkDecision,
)
from .pipeline import STATE_NAME, _load_state
from .reporting import (
    DeepSeekStatistics,
    build_configuration_identity,
    build_manifest,
    build_processing_report,
)
from .storage import (
    atomic_write_jsonl,
    load_audit,
    load_chunks,
    load_manifest,
    write_document_outputs,
)
from .taxonomy import load_taxonomy
from .validation import validate_document_chunks


REVIEWABLE_DECISIONS = {
    AuditDecision.NEEDS_REVIEW,
    AuditDecision.LLM_FAILED,
    AuditDecision.VALIDATION_FAILED,
}


class ReviewDecision(BaseModel):
    """审核人员只能提供结构化的范围元数据。"""

    model_config = ConfigDict(extra="forbid")

    audit_id: str
    action: Literal["approve", "exclude", "edit", "retry"]
    start_atom_id: str | None = None
    end_atom_id: str | None = None
    type: ChunkType | None = None
    topic_tags: list[str] | None = None
    summary: str | None = None
    reviewer: str | None = None
    review_note: str | None = None

    @model_validator(mode="after")
    def require_chunk_fields(self) -> "ReviewDecision":
        if self.action in {"approve", "edit"} and (
            not self.start_atom_id
            or not self.end_atom_id
            or self.type is None
            or not self.topic_tags
            or not self.summary
        ):
            raise ValueError("approve/edit require a range, type, topic_tags, and summary")
        return self


@dataclass(frozen=True, slots=True)
class ReviewApplyResult:
    audit_records: list[AuditRecord]
    rejected_fields: list[str]


def _resolved_parent_ids(records: Sequence[AuditRecord]) -> set[str]:
    return {
        record.parent_audit_id
        for record in records
        if record.decision is AuditDecision.MANUALLY_RESOLVED and record.parent_audit_id
    }


def _unresolved(records: Sequence[AuditRecord]) -> list[AuditRecord]:
    resolved = _resolved_parent_ids(records)
    return [
        record
        for record in records
        if record.decision in REVIEWABLE_DECISIONS and record.audit_id not in resolved
    ]


def export_review_queue(output_dir: Path, review_file: Path) -> list[AuditRecord]:
    """仅将仍未解决的审计项写入用户管理的 JSONL 队列。"""

    queue = _unresolved(load_audit(Path(output_dir) / "audit.jsonl"))
    atomic_write_jsonl(Path(review_file), queue)
    return queue


def _atoms_for_range(document, start_atom_id: str, end_atom_id: str):
    atoms = sorted(document.atoms, key=lambda atom: (atom.start_char, atom.end_char))
    positions = {atom.atom_id: index for index, atom in enumerate(atoms)}
    if start_atom_id not in positions or end_atom_id not in positions:
        raise ValueError("review range refers to an unknown atom")
    start, end = positions[start_atom_id], positions[end_atom_id]
    if start > end:
        raise ValueError("review range is out of source order")
    selected = atoms[start : end + 1]
    if len({(atom.section_id, tuple(atom.section_path)) for atom in selected}) != 1:
        raise ValueError("review range cannot cross a deepest section")
    return selected


def _manual_audit(
    original: AuditRecord,
    decision: ReviewDecision,
    atoms,
    *,
    chunk_id: str | None,
) -> AuditRecord:
    candidate = any(atom.recommendation_candidate for atom in atoms)
    coverage = (
        RecommendationCoverageStatus.COVERED
        if decision.action in {"approve", "edit"} and candidate
        else RecommendationCoverageStatus.NOT_IN_SCOPE
        if decision.action == "exclude" and candidate
        else RecommendationCoverageStatus.NOT_CANDIDATE
    )
    return AuditRecord(
        audit_id=f"AUD_{uuid4().hex.upper()}",
        run_id=original.run_id,
        source_doc=original.source_doc,
        batch_id=original.batch_id,
        section_id=atoms[0].section_id,
        section_path=list(atoms[0].section_path),
        start_atom_id=atoms[0].atom_id,
        end_atom_id=atoms[-1].atom_id,
        source_excerpt="",  # 由调用方替换为精确的文档切片
        decision=AuditDecision.MANUALLY_RESOLVED,
        reason_code=("MANUAL_EXCLUDE" if decision.action == "exclude" else "MANUAL_APPROVE"),
        decision_reason=decision.review_note or decision.reviewer or "manual review decision",
        candidate_type=decision.type,
        final_type=decision.type if decision.action != "exclude" else None,
        chunk_id=chunk_id,
        recommendation_candidate=candidate,
        recommendation_coverage_status=coverage,
        attempt_count=original.attempt_count,
        validation_errors=[],
        created_at=datetime.now(timezone.utc),
        parent_audit_id=original.audit_id,
    )


def _rebuild_chunks(document, existing_chunks, records, decisions):
    """按原文顺序重建全部知识块，以便重新验证 ID 和精确范围。"""

    audit_for_chunk = {
        record.chunk_id: record
        for record in records
        if record.chunk_id and record.decision in {AuditDecision.ACCEPTED, AuditDecision.MANUALLY_RESOLVED}
    }
    specifications = []
    for chunk in existing_chunks:
        audit = audit_for_chunk.get(chunk.chunk_id)
        if audit is None or not audit.start_atom_id or not audit.end_atom_id:
            raise ValueError(f"chunk {chunk.chunk_id} has no reconstructable audit range")
        specifications.append(
            (audit.start_atom_id, audit.end_atom_id, chunk.type, chunk.topic_tags, chunk.summary, None)
        )
    for original, decision in decisions:
        if decision.action in {"approve", "edit"}:
            specifications.append(
                (
                    decision.start_atom_id,
                    decision.end_atom_id,
                    decision.type,
                    decision.topic_tags,
                    decision.summary,
                    original.audit_id,
                )
            )
    positions = {atom.atom_id: atom.start_char for atom in document.atoms}
    specifications.sort(key=lambda item: positions[item[0]])
    atom_index = {atom.atom_id: atom for atom in document.atoms}
    registry = DocumentChunkIdRegistry(document.source_doc)
    chunks, spans, created = [], {}, {}
    for start, end, chunk_type, tags, summary, parent_id in specifications:
        semantic = SemanticChunkDecision(
            start_atom_id=start,
            end_atom_id=end,
            type=chunk_type,
            topic_tags=tags,
            summary=summary,
            decision_reason="manual reconstruction",
        )
        chunk, span = assemble_semantic_chunk(
            document.source_doc,
            document.text,
            semantic,
            atom_index,
            id_registry=registry,
        )
        chunks.append(chunk)
        spans[chunk.chunk_id] = span
        if parent_id:
            created[parent_id] = (chunk, span)
    return chunks, spans, created


def apply_review_decisions(
    output_dir: Path,
    decisions: Sequence[ReviewDecision | Mapping[str, object]],
) -> ReviewApplyResult:
    """追加处理结果，并以原子方式重新发布已完整复验的输出集。"""

    root = Path(output_dir)
    records = load_audit(root / "audit.jsonl")
    rejected = sorted(
        {
            key
            for value in decisions
            if isinstance(value, Mapping)
            for key in value
            if key not in ReviewDecision.model_fields
        }
    )
    if rejected:
        return ReviewApplyResult(records, rejected)
    parsed = [
        value if isinstance(value, ReviewDecision) else ReviewDecision.model_validate(value)
        for value in decisions
    ]
    if any(value.action == "retry" for value in parsed):
        raise ValueError("retry decisions require a semantic client and are not applied offline")
    unresolved = {record.audit_id: record for record in _unresolved(records)}
    if len({value.audit_id for value in parsed}) != len(parsed):
        raise ValueError("each unresolved audit may be resolved only once")
    if any(value.audit_id not in unresolved for value in parsed):
        raise ValueError("review decision must reference an unresolved audit")

    state = _load_state(root / STATE_NAME)
    config = load_pipeline_config(Path(state["config_path"]))
    taxonomy = load_taxonomy(config.taxonomy.path)
    document = parse_source_document(Path(state["input_path"]))
    from .recommendations import mark_recommendation_candidates

    document = document.model_copy(update={"atoms": mark_recommendation_candidates(document.atoms)})
    resolved_pairs = [(unresolved[value.audit_id], value) for value in parsed]
    chunks, spans, created = _rebuild_chunks(document, load_chunks(root / "chunks.jsonl"), records, resolved_pairs)
    new_records = list(records)
    for original, decision in resolved_pairs:
        start = decision.start_atom_id or original.start_atom_id
        end = decision.end_atom_id or original.end_atom_id
        if not start or not end:
            raise ValueError("review decision has no source range")
        atoms = _atoms_for_range(document, start, end)
        item = created.get(original.audit_id)
        manual = _manual_audit(original, decision, atoms, chunk_id=item[0].chunk_id if item else None)
        manual = manual.model_copy(update={"source_excerpt": document.text[atoms[0].start_char : atoms[-1].end_char]})
        new_records.append(manual)

    resolved_ids = _resolved_parent_ids(new_records)
    active = [record for record in new_records if record.decision in REVIEWABLE_DECISIONS and record.audit_id not in resolved_ids]
    disposition = {atom.atom_id for atom in document.atoms}
    covered = set()
    for record in new_records:
        if record.decision not in {AuditDecision.ACCEPTED, AuditDecision.EXCLUDED, AuditDecision.MANUALLY_RESOLVED}:
            continue
        if not record.start_atom_id or not record.end_atom_id:
            continue
        covered.update(
            atom.atom_id
            for atom in _atoms_for_range(document, record.start_atom_id, record.end_atom_id)
            if atom.recommendation_candidate
        )
    issues = validate_document_chunks(
        document,
        chunks,
        spans,
        allowed_tags=taxonomy.allowed_tags,
        disposition_atom_ids=disposition,
        recommendation_covered_atom_ids=covered,
        unresolved_audit_records=active,
        summary_max_chars=config.summary.max_chars,
        review_max_chars=config.chunking.review_max_chars,
    )
    previous_manifest = load_manifest(root / "manifest.json")
    completed_at = datetime.now(timezone.utc)
    report = build_processing_report(
        document,
        chunks,
        new_records,
        run_id=previous_manifest.run_id,
        status=RunStatus.COMPLETED,
        validation_issues=issues,
        disposition_atom_ids=disposition,
        recommendation_covered_atom_ids=covered,
        deepseek=DeepSeekStatistics(),
        started_at=previous_manifest.started_at,
        completed_at=completed_at,
    )
    status = RunStatus.COMPLETED if report.quality_gate_passed else RunStatus.COMPLETED_WITH_REVIEW
    report = report.model_copy(update={"status": status})
    manifest = build_manifest(
        document,
        run_id=previous_manifest.run_id,
        status=status,
        started_at=previous_manifest.started_at,
        completed_at=completed_at,
        configuration=build_configuration_identity(config, taxonomy),
        chunks=chunks,
        audit_records=new_records,
    )
    write_document_outputs(root, chunks, manifest, new_records, report)
    return ReviewApplyResult(new_records, [])
