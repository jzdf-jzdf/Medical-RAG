"""文档输出的 UTF-8 存储与事务式发布。"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from .models import AuditDecision, AuditRecord, FactChunk, RecommendationChunk
from .reporting import (
    Manifest,
    OutputNames,
    ProcessingReport,
    compute_quality_gate,
)


Chunk = FactChunk | RecommendationChunk
_SECRET_TOKEN = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]+\b")
_SECRET_ASSIGNMENT = re.compile(
    r"(?ix)"
    r"(?P<prefix>"
    r"[\"']?[a-z0-9_-]*"
    r"(?:api[_-]?key|authorization|access[_-]?token|token|secret|password)"
    r"[a-z0-9_-]*[\"']?"
    r"(?:\s*[:=]\s*|\s+)"
    r")"
    r"(?:"
    r"(?P<quote>[\"'])(?P<quoted>.*?)(?P=quote)"
    r"|(?:bearer\s+)?(?P<unquoted>[^\s,;}\"']+)"
    r")"
)


@dataclass(frozen=True, slots=True)
class OutputPaths:
    chunks: Path
    manifest: Path
    audit: Path
    processing_report: Path

    def __iter__(self) -> Iterator[Path]:
        yield self.chunks
        yield self.manifest
        yield self.audit
        yield self.processing_report


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return value


def _json_text(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, indent=2) + "\n"


def _jsonl_text(records: Iterable[Any]) -> str:
    return "".join(
        json.dumps(
            _jsonable(record),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
        for record in records
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys)


def _write_staged(path: Path, payload: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write_json(path: Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        _write_staged(temporary, _json_text(value))
        _parse_json(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_jsonl(path: Path, records: Iterable[Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    values = list(records)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        _write_staged(temporary, _jsonl_text(values))
        for line in temporary.read_text(encoding="utf-8").splitlines():
            _parse_json(line)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_json(path: Path) -> Any:
    return _parse_json(Path(path).read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[Any]:
    values = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"blank JSONL line {number}")
        values.append(_parse_json(line))
    return values


def load_chunks(path: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for value in _load_jsonl(path):
        if not isinstance(value, dict):
            raise ValueError("chunk JSONL records must be objects")
        if value.get("type") == "fact":
            chunks.append(FactChunk.model_validate(value))
        elif value.get("type") == "recommendation":
            chunks.append(RecommendationChunk.model_validate(value))
        else:
            raise ValueError("unknown chunk type")
    return chunks


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate(_load_json(path))


def load_audit(path: Path) -> list[AuditRecord]:
    return [AuditRecord.model_validate(value) for value in _load_jsonl(path)]


def load_processing_report(path: Path) -> ProcessingReport:
    return ProcessingReport.model_validate(_load_json(path))


def _redact_string(value: str) -> str:
    def replace_assignment(match: re.Match[str]) -> str:
        quote = match.group("quote")
        replacement = f"{quote}[REDACTED]{quote}" if quote else "[REDACTED]"
        return match.group("prefix") + replacement

    value = _SECRET_ASSIGNMENT.sub(replace_assignment, value)
    return _SECRET_TOKEN.sub("[REDACTED]", value)


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _sanitize_audits(records: Sequence[AuditRecord]) -> list[AuditRecord]:
    return [
        AuditRecord.model_validate(_sanitize(record.model_dump(mode="json")))
        for record in records
    ]


def _validate_audit_sequence(records: Sequence[AuditRecord]) -> None:
    seen: set[str] = set()
    for record in records:
        if record.audit_id in seen:
            raise ValueError(f"duplicate audit_id: {record.audit_id}")
        if record.decision is AuditDecision.MANUALLY_RESOLVED:
            if record.parent_audit_id not in seen:
                raise ValueError("parent_audit_id must reference an earlier audit record")
        seen.add(record.audit_id)


def _validate_output_set(
    chunks: Sequence[Chunk],
    manifest: Manifest,
    audits: Sequence[AuditRecord],
    report: ProcessingReport,
) -> None:
    facts = sum(isinstance(chunk, FactChunk) for chunk in chunks)
    recommendations = sum(isinstance(chunk, RecommendationChunk) for chunk in chunks)
    expected_counts = {
        "chunks": len(chunks),
        "fact": facts,
        "recommendation": recommendations,
        "audit_records": len(audits),
    }
    if manifest.counts.model_dump() != expected_counts:
        raise ValueError("manifest chunk counts do not match output records")
    if report.chunks.model_dump() != {
        "total": len(chunks),
        "fact": facts,
        "recommendation": recommendations,
    }:
        raise ValueError("processing report chunk counts do not match output records")
    actual_decisions = Counter(record.decision.value for record in audits)
    expected_decisions = {
        decision.value: actual_decisions[decision.value] for decision in AuditDecision
    }
    if report.decisions.model_dump() != expected_decisions:
        raise ValueError("processing report decision counts do not match audit records")
    expected_topics = dict(
        sorted(Counter(tag for chunk in chunks for tag in chunk.topic_tags).items())
    )
    if report.topic_distribution != expected_topics:
        raise ValueError("processing report topic distribution does not match chunks")
    if manifest.outputs != OutputNames():
        raise ValueError("manifest output names do not match output contract")
    if manifest.run_id != report.run_id or manifest.status != report.status:
        raise ValueError("manifest and processing report run identity mismatch")
    if any(chunk.source_doc != manifest.source_doc for chunk in chunks):
        raise ValueError("chunk source_doc does not match manifest")
    if any(
        record.source_doc != manifest.source_doc or record.run_id != manifest.run_id
        for record in audits
    ):
        raise ValueError("audit identity does not match manifest")
    if report.source_doc != manifest.source_doc:
        raise ValueError("processing report source_doc does not match manifest")
    if report.quality_gate_passed != compute_quality_gate(report.quality_metrics):
        raise ValueError("processing report quality gate is inconsistent with metrics")
    if manifest.status.value == "completed" and not report.quality_gate_passed:
        raise ValueError("completed status requires a passing quality gate")
    _validate_audit_sequence(audits)


def write_document_outputs(
    output_dir: Path,
    chunks: Sequence[Chunk],
    manifest: Manifest,
    audit_records: Sequence[AuditRecord],
    processing_report: ProcessingReport,
) -> OutputPaths:
    """验证、暂存并以事务方式发布四项正式输出。"""

    chunks = list(chunks)
    audits = _sanitize_audits(list(audit_records))
    _validate_output_set(chunks, manifest, audits, processing_report)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = OutputPaths(
        chunks=root / "chunks.jsonl",
        manifest=root / "manifest.json",
        audit=root / "audit.jsonl",
        processing_report=root / "processing_report.json",
    )
    payloads = {
        paths.chunks: _jsonl_text(chunks),
        paths.manifest: _json_text(manifest),
        paths.audit: _jsonl_text(audits),
        paths.processing_report: _json_text(processing_report),
    }
    transaction_id = uuid4().hex
    staged = {
        destination: destination.with_name(f".{destination.name}.{transaction_id}.tmp")
        for destination in payloads
    }
    backups = {
        destination: destination.with_name(f".{destination.name}.{transaction_id}.bak")
        for destination in payloads
    }
    backed_up: list[Path] = []
    published: list[Path] = []
    transaction_completed = False
    rollback_completed = False
    try:
        for destination, payload in payloads.items():
            _write_staged(staged[destination], payload)

        load_chunks(staged[paths.chunks])
        load_manifest(staged[paths.manifest])
        load_audit(staged[paths.audit])
        load_processing_report(staged[paths.processing_report])

        for destination in payloads:
            if destination.exists():
                os.replace(destination, backups[destination])
                backed_up.append(destination)
        for destination in payloads:
            os.replace(staged[destination], destination)
            published.append(destination)
        transaction_completed = True
    except Exception as publish_error:
        rollback_errors: list[Exception] = []
        for destination in reversed(published):
            try:
                if destination.exists():
                    destination.unlink()
            except Exception as error:  # pragma: no cover - 灾难性文件系统故障
                rollback_errors.append(error)
        for destination in reversed(backed_up):
            backup = backups[destination]
            try:
                if backup.exists():
                    os.replace(backup, destination)
            except Exception as error:  # pragma: no cover - 灾难性文件系统故障
                rollback_errors.append(error)
        rollback_completed = not rollback_errors
        if rollback_errors:
            raise RuntimeError(
                "output publication failed and rollback was incomplete; "
                "recovery backups were preserved"
            ) from publish_error
        raise
    finally:
        for temporary in staged.values():
            if temporary.exists():
                temporary.unlink()
        if transaction_completed or rollback_completed:
            for backup in backups.values():
                if backup.exists():
                    backup.unlink()
    return paths
