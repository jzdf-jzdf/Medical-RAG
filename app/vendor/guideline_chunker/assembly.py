"""依据原文精确重建已验证的语义切块决策。"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from .identifiers import make_unique_chunk_id
from .models import (
    FactChunk,
    RecommendationChunk,
    SemanticBatch,
    SemanticBatchResponse,
    SemanticChunkDecision,
    SourceAtom,
    SourceDocument,
)
from .recommendations import extract_recommendation_levels
from .validation import ValidationIssue, validate_semantic_response


Chunk = FactChunk | RecommendationChunk


class AssemblyError(ValueError):
    """当未验证的语义响应被提交组装时抛出。"""

    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = list(issues)
        super().__init__("; ".join(str(issue) for issue in self.issues))


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    chunks: list[Chunk]
    chunk_spans: dict[str, tuple[int, int]]
    disposition_atom_ids: tuple[str, ...]


@dataclass(slots=True)
class DocumentChunkIdRegistry:
    """按原文位置单调递增顺序登记的单文档冲突状态。

    每份指南创建一个注册表，并由按原文顺序调用的 ``assemble_batch`` 共享。
    不要按并发任务的完成顺序登记批次。
    """

    source_doc: str
    _payloads: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)
    _last_source_start: int | None = field(default=None, init=False, repr=False)

    def issue(
        self,
        chunk_type: str,
        section_id: str,
        content: str,
        *,
        source_start: int,
    ) -> str:
        if (
            self._last_source_start is not None
            and source_start < self._last_source_start
        ):
            raise ValueError(
                "document chunk IDs must be registered in source order: "
                f"{source_start} < {self._last_source_start}"
            )
        payload = (self.source_doc, chunk_type, section_id, content)
        chunk_id = make_unique_chunk_id(*payload, self._payloads)
        self._payloads[chunk_id] = payload
        self._last_source_start = source_start
        return chunk_id


def _chunk_type(item: SemanticChunkDecision) -> str:
    return str(getattr(item.type, "value", item.type))


def assemble_semantic_chunk(
    source_doc: str,
    source_text: str,
    item: SemanticChunkDecision,
    atom_index: Mapping[str, SourceAtom],
    *,
    existing_payloads: Mapping[str, Sequence[str]] | None = None,
    id_registry: DocumentChunkIdRegistry | None = None,
) -> tuple[Chunk, tuple[int, int]]:
    """从恰好一个连续原文切片创建一个带类型的知识块。"""

    first = atom_index[item.start_atom_id]
    last = atom_index[item.end_atom_id]
    start, end = first.start_char, last.end_char
    content = source_text[start:end]
    chunk_type = _chunk_type(item)
    if existing_payloads is not None and id_registry is not None:
        raise ValueError("pass existing_payloads or id_registry, not both")
    if id_registry is not None:
        if id_registry.source_doc != source_doc:
            raise ValueError("id_registry belongs to a different source document")
        chunk_id = id_registry.issue(
            chunk_type,
            first.section_id,
            content,
            source_start=start,
        )
    else:
        payloads = existing_payloads if existing_payloads is not None else {}
        chunk_id = make_unique_chunk_id(
            source_doc,
            chunk_type,
            first.section_id,
            content,
            payloads,
        )
    common = {
        "chunk_id": chunk_id,
        "source_doc": source_doc,
        "section_id": first.section_id,
        "section_path": list(first.section_path),
        "type": chunk_type,
        "topic_tags": list(item.topic_tags),
        "summary": item.summary,
        "content": content,
    }
    if chunk_type == "recommendation":
        chunk: Chunk = RecommendationChunk(
            **common,
            recommendation_levels=extract_recommendation_levels(content),
        )
    else:
        chunk = FactChunk(**common)
    return chunk, (start, end)


def _allowed_tags(value: Collection[str] | object) -> frozenset[str]:
    candidate = getattr(value, "allowed_tags", value)
    return frozenset(candidate)  # type: ignore[arg-type]


def assemble_batch(
    document: SourceDocument,
    batch: SemanticBatch,
    response: SemanticBatchResponse,
    allowed_tags: Collection[str] | object | None = None,
    *,
    taxonomy: object | None = None,
    id_registry: DocumentChunkIdRegistry | None = None,
    summary_max_chars: int = 120,
    review_max_chars: int = 500,
) -> AssemblyResult:
    """组装一个批次；同一指南按原文顺序共享 ``id_registry``。"""

    if allowed_tags is not None and taxonomy is not None:
        raise ValueError("pass allowed_tags or taxonomy, not both")
    tag_contract = taxonomy if taxonomy is not None else allowed_tags
    if tag_contract is None:
        raise ValueError("allowed_tags or taxonomy is required")
    atom_index = {atom.atom_id: atom for atom in document.atoms}
    issues = validate_semantic_response(
        batch,
        response,
        atom_index,
        _allowed_tags(tag_contract),
        summary_max_chars=summary_max_chars,
        review_max_chars=review_max_chars,
    )
    if issues:
        raise AssemblyError(issues)

    chunks: list[Chunk] = []
    spans: dict[str, tuple[int, int]] = {}
    registry = id_registry or DocumentChunkIdRegistry(document.source_doc)
    if registry.source_doc != document.source_doc:
        raise ValueError("id_registry belongs to a different source document")
    ordered_decisions = sorted(
        response.chunks,
        key=lambda item: atom_index[item.start_atom_id].start_char,
    )
    for item in ordered_decisions:
        chunk, span = assemble_semantic_chunk(
            document.source_doc,
            document.text,
            item,
            atom_index,
            id_registry=registry,
        )
        chunks.append(chunk)
        spans[chunk.chunk_id] = span
    return AssemblyResult(
        chunks=chunks,
        chunk_spans=spans,
        disposition_atom_ids=tuple(batch.core_atom_ids),
    )
