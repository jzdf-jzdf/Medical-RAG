"""对语义决策和已组装知识块进行确定性验证。"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from .identifiers import make_unique_chunk_id
from .models import (
    FactChunk,
    RecommendationChunk,
    SemanticBatch,
    SemanticBatchResponse,
    SemanticChunkDecision,
    SemanticRangeDecision,
    SourceAtom,
    SourceDocument,
)
from .recommendations import extract_recommendation_levels


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """一条稳定且机器可读的验证问题。"""

    code: str
    atom_id: str | None = None
    detail: str | None = None

    def __str__(self) -> str:
        location = f" [{self.atom_id}]" if self.atom_id else ""
        detail = f": {self.detail}" if self.detail else ""
        return f"{self.code}{location}{detail}"


_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*")
_ASCII_UNIT_PATTERN = (
    r"mm\s*hg|mmol(?:/l)?|kpa|mg|μg|ug|kg|ml|min|cm|mm"
)
_CHINESE_UNIT_PATTERN = (
    r"小时|分钟|秒|(?<![今明昨])天(?!然)|周(?!围)|月(?!经)|"
    r"(?<![老中青少])年(?!龄)|岁|(?<!其)次(?!要)|例(?!如)"
)
_UNIT_AFTER_NUMBER_RE = re.compile(
    rf"(?i)\s*(?P<unit>(?:{_ASCII_UNIT_PATTERN})(?![A-Za-z])|"
    rf"(?:{_CHINESE_UNIT_PATTERN})|[%％])"
)
_UNIT_RE = re.compile(
    rf"(?i)(?<![A-Za-z])(?:{_ASCII_UNIT_PATTERN})(?![A-Za-z])|"
    rf"(?:{_CHINESE_UNIT_PATTERN})|[%％]"
)
_INDICATOR_RE = re.compile(
    r"平均动脉压|收缩压|舒张压|颅内压|灌注压|脉压|血压|"
    r"心率|脉搏|体温|血糖|血钠|血钾|血钙|血镁|"
    r"剂量|浓度|直径|体积|厚度|评分|年龄|时间|时长|间隔|"
    r"发生率|死亡率|比例|风险"
)
_ACTION_DIRECTION_RE = re.compile(
    r"(?P<negative>不推荐|不建议|不应|禁用|禁止|不宜|不可|不能|"
    r"不需要|不必|避免|无须|无需)|"
    r"(?P<positive>(?<!级)推荐|建议|应当|可以|需要|须|"
    r"应(?=[将对在予使采行实监控给选考维接启停治])|"
    r"可(?=[将对在予使采行实监控给选考维接启停治])|"
    r"需(?=[将对在予使采行实监控给选考维接启停治]))"
)
_ACTION_VERB_RE = re.compile(
    r"用于|使用|给予|应用|采用|施用|用药|治疗|监测|控制|停用"
)
_ADMINISTRATION_VERB_RE = re.compile(r"使用|给予|应用|采用|施用")
_FACT_NEGATION_RE = re.compile(
    r"尚缺乏|缺乏|证据不足|不确定|未证实|无证据|"
    r"并非|无关|否认|未见|未发现|不存在|没有"
)
_CLAUSE_RE = re.compile(r"[^，,；;。！？!?\n]+")
_RECOMMENDATION_METADATA_RE = re.compile(
    r"[（(][^）)]*(?:级推荐|级证据)[^）)]*[）)]"
)
_SENTENCE_END_RE = re.compile(r"[。！？]")
_CITATION_RE = re.compile(
    r"[\[［【]\s*\d+(?:\s*[-–—,，、]\s*\d+)*\s*[\]］】]"
)
_LIST_ENUMERATOR_RE = re.compile(r"[（(]\s*\d{1,3}\s*[）)]")
_STANDALONE_UNIT_CONTEXT_RE = re.compile(r"单位|计量|记录|采用")
_CITATION_RE = re.compile(
    r"[\[［【]\s*\d+(?:\s*[-–—,，、]\s*\d+)*\s*[\]］】]"
)
_LIST_ENUMERATOR_RE = re.compile(r"[（(]\s*\d{1,3}\s*[）)]")
_STANDALONE_UNIT_CONTEXT_RE = re.compile(r"单位|计量|记录|采用")


def _ordered_atoms(atom_index: Mapping[str, SourceAtom]) -> list[SourceAtom]:
    return sorted(atom_index.values(), key=lambda atom: (atom.start_char, atom.end_char))


def _source_for_atoms(atoms: Sequence[SourceAtom]) -> str:
    """从原子载荷构建验证文本，绝不使用最终知识块内容。"""

    return "".join(atom.text for atom in atoms)


def _match_is_within(match: re.Match[str], spans: Sequence[re.Match[str]]) -> bool:
    return any(
        container.start() <= match.start() and match.end() <= container.end()
        for container in spans
    )


def _is_clinical_number(
    text: str,
    match: re.Match[str],
    unit: str | None,
    *,
    citation_spans: Sequence[re.Match[str]],
    enumerator_spans: Sequence[re.Match[str]],
) -> bool:
    """排除引用、序号、年份和邮编等非临床数字。"""

    if _match_is_within(match, citation_spans) or _match_is_within(
        match, enumerator_spans
    ):
        return False

    raw = match.group(0).replace(" ", "")
    integer_text = raw.replace(",", "")
    if integer_text.isdigit():
        integer = int(integer_text)
        if len(integer_text) == 4 and 1900 <= integer <= 2099:
            return False

    if unit is not None:
        return True

    clause_start = max(
        text.rfind(separator, 0, match.start())
        for separator in ("，", ",", "；", ";", "。", "！", "？", "\n")
    )
    local_prefix = text[clause_start + 1 : match.start()]
    if _INDICATOR_RE.search(local_prefix):
        return True
    if re.search(
        r"(?:n\s*=|约为|至少为|至多为|不超过|不低于|低于|高于|"
        r"小于|大于|达到|控制在|降至|升至|[<>≤≥=])\s*$",
        local_prefix,
        flags=re.IGNORECASE,
    ):
        return True

    # 五位及以上、没有临床单位或指标绑定的整数通常是邮编、编号或标识符。
    if integer_text.isdigit() and len(integer_text) >= 5:
        return False
    return True


def _numeric_sequence(
    text: str,
    *,
    clinical_only: bool = False,
) -> list[tuple[str, str | None]]:
    sequence: list[tuple[str, str | None]] = []
    citation_spans = list(_CITATION_RE.finditer(text))
    enumerator_spans = list(_LIST_ENUMERATOR_RE.finditer(text))
    for match in _NUMBER_RE.finditer(text):
        number = match.group(0).replace(" ", "")
        unit_match = _UNIT_AFTER_NUMBER_RE.match(text, match.end())
        unit = (
            unit_match.group("unit").replace(" ", "").casefold()
            if unit_match is not None
            else None
        )
        if clinical_only and not _is_clinical_number(
            text,
            match,
            unit,
            citation_spans=citation_spans,
            enumerator_spans=enumerator_spans,
        ):
            continue
        sequence.append((number, unit))
    return sequence


def _numbers(sequence: Sequence[tuple[str, str | None]]) -> list[str]:
    return [number for number, _ in sequence]


def _meaningful_units(text: str) -> list[str]:
    """返回与临床数值或明确计量语境绑定的单位。"""

    units = [unit for _, unit in _numeric_sequence(text, clinical_only=True) if unit]
    for match in _UNIT_RE.finditer(text):
        if _UNIT_AFTER_NUMBER_RE.search(text, 0, match.end()) is not None:
            preceding = text[: match.start()]
            number_match = list(_NUMBER_RE.finditer(preceding))
            if number_match:
                nearest = number_match[-1]
                attached = _UNIT_AFTER_NUMBER_RE.match(text, nearest.end())
                if attached is not None and attached.start("unit") == match.start():
                    continue
        clause_start = max(
            text.rfind(separator, 0, match.start())
            for separator in ("，", ",", "；", ";", "。", "！", "？", "\n")
        )
        clause_end_candidates = [
            position
            for separator in ("，", ",", "；", ";", "。", "！", "？", "\n")
            if (position := text.find(separator, match.end())) >= 0
        ]
        clause_end = min(clause_end_candidates) if clause_end_candidates else len(text)
        clause = text[clause_start + 1 : clause_end]
        if _STANDALONE_UNIT_CONTEXT_RE.search(clause):
            units.append(match.group(0).replace(" ", "").casefold())
    return units


def _numeric_object(text: str, number_start: int) -> str:
    prefix = re.split(r"[，,；;。！？!?\n]", text[:number_start])[-1]
    indicators = list(_INDICATOR_RE.finditer(prefix))
    if indicators:
        return indicators[-1].group(0)
    prefix = re.sub(
        r"(?:约为|至少为|至多为|不超过|不低于|低于|高于|小于|大于|"
        r"控制在|降至|升至|达到|达|为|是|约|近|[<>≤≥=])\s*$",
        "",
        prefix.strip(),
    )
    prefix = re.sub(r"^(?:患者|受试者|患儿|成人|老年人)", "", prefix)
    return re.sub(r"\s+", "", prefix)[-12:]


def _numeric_bindings(
    text: str,
    *,
    clinical_only: bool = False,
) -> Counter[tuple[str, str, str | None]]:
    bindings: Counter[tuple[str, str, str | None]] = Counter()
    citation_spans = list(_CITATION_RE.finditer(text))
    enumerator_spans = list(_LIST_ENUMERATOR_RE.finditer(text))
    for match in _NUMBER_RE.finditer(text):
        unit_match = _UNIT_AFTER_NUMBER_RE.match(text, match.end())
        unit = (
            unit_match.group("unit").replace(" ", "").casefold()
            if unit_match is not None
            else None
        )
        if clinical_only and not _is_clinical_number(
            text,
            match,
            unit,
            citation_spans=citation_spans,
            enumerator_spans=enumerator_spans,
        ):
            continue
        bindings[(_numeric_object(text, match.start()), match.group(0), unit)] += 1
    return bindings


def _canonical_action_body(value: str) -> str:
    value = _ADMINISTRATION_VERB_RE.sub("使用", value)
    value = _NUMBER_RE.sub("<number>", value)
    value = _UNIT_RE.sub("<unit>", value)
    value = re.sub(r"^(?:但|且|并|同时)", "", value)
    return re.sub(r"\s+", "", value).strip("：:")


def _action_modalities(text: str) -> Counter[tuple[str, str]]:
    """返回与局部动作绑定的肯定、否定和事实模态。"""

    modalities: Counter[tuple[str, str]] = Counter()
    action_text = _RECOMMENDATION_METADATA_RE.sub("", text)
    for clause_match in _CLAUSE_RE.finditer(action_text):
        clause = clause_match.group(0).strip()
        direction_matches = list(_ACTION_DIRECTION_RE.finditer(clause))
        for match in direction_matches:
            marker = match.group(0)
            replacement = "使用" if marker in {"禁用", "禁止"} else ""
            body = f"{clause[:match.start()]}{replacement}{clause[match.end():]}"
            body = _canonical_action_body(body)
            modality = "negative" if match.lastgroup == "negative" else "positive"
            modalities[(body, modality)] += 1
        if not direction_matches and _ACTION_VERB_RE.search(clause):
            modalities[(_canonical_action_body(clause), "factual")] += 1
    return modalities


def _append_polarity_issues(
    issues: list[ValidationIssue],
    summary: str,
    source: str,
    atom_id: str | None,
) -> None:
    source_modalities = _action_modalities(source)
    summary_modalities = _action_modalities(summary)
    if source_modalities != summary_modalities:
        issues.append(
            ValidationIssue("SUMMARY_ACTION_MODALITY_MISMATCH", atom_id=atom_id)
        )

    bodies = {body for body, _ in source_modalities | summary_modalities}
    for body in sorted(bodies):
        source_negative = source_modalities[(body, "negative")]
        summary_negative = summary_modalities[(body, "negative")]
        if source_negative > summary_negative:
            issues.append(
                ValidationIssue("SUMMARY_NEGATION_MISMATCH", atom_id=atom_id, detail=body)
            )
        elif summary_negative > source_negative:
            issues.append(
                ValidationIssue("SUMMARY_NEW_NEGATION", atom_id=atom_id, detail=body)
            )

    source_fact_negative = len(_FACT_NEGATION_RE.findall(source))
    summary_fact_negative = len(_FACT_NEGATION_RE.findall(summary))
    if source_fact_negative > summary_fact_negative:
        issues.append(ValidationIssue("SUMMARY_NEGATION_MISMATCH", atom_id=atom_id))
    elif summary_fact_negative > source_fact_negative:
        issues.append(ValidationIssue("SUMMARY_NEW_NEGATION", atom_id=atom_id))


def validate_summary(
    summary: str,
    source: str,
    *,
    atom_id: str | None = None,
    max_chars: int = 120,
) -> list[ValidationIssue]:
    """检查摘要长度是否受限，以及其中的硬事实是否忠实于原文。"""

    issues: list[ValidationIssue] = []
    value = summary.strip()
    if not value:
        return [ValidationIssue("SUMMARY_EMPTY", atom_id=atom_id)]
    if len(value) > max_chars:
        issues.append(
            ValidationIssue(
                "SUMMARY_TOO_LONG",
                atom_id=atom_id,
                detail=f"{len(value)} > {max_chars}",
            )
        )
    if len(_SENTENCE_END_RE.findall(value)) > 2:
        issues.append(ValidationIssue("SUMMARY_TOO_MANY_SENTENCES", atom_id=atom_id))

    source_numeric = _numeric_sequence(source)
    required_source_numeric = _numeric_sequence(source, clinical_only=True)
    summary_numeric = _numeric_sequence(value)
    source_numbers = _numbers(source_numeric)
    required_source_numbers = _numbers(required_source_numeric)
    summary_numbers = _numbers(summary_numeric)
    source_number_counts = Counter(source_numbers)
    required_source_number_counts = Counter(required_source_numbers)
    summary_number_counts = Counter(summary_numbers)
    for number in sorted((summary_number_counts - source_number_counts).elements()):
        issues.append(
            ValidationIssue("SUMMARY_NEW_NUMBER", atom_id=atom_id, detail=number)
        )
    for number in sorted(
        (required_source_number_counts - summary_number_counts).elements()
    ):
        issues.append(
            ValidationIssue("SUMMARY_MISSING_NUMBER", atom_id=atom_id, detail=number)
        )
    source_units = _meaningful_units(source)
    summary_units = _meaningful_units(value)
    source_unit_counts = Counter(source_units)
    summary_unit_counts = Counter(summary_units)
    for unit in sorted((summary_unit_counts - source_unit_counts).elements()):
        issues.append(
            ValidationIssue("SUMMARY_NEW_UNIT", atom_id=atom_id, detail=unit)
        )
    for unit in sorted((source_unit_counts - summary_unit_counts).elements()):
        issues.append(
            ValidationIssue("SUMMARY_MISSING_UNIT", atom_id=atom_id, detail=unit)
        )
    if (
        required_source_number_counts == summary_number_counts
        and source_unit_counts == summary_unit_counts
        and _numeric_bindings(source, clinical_only=True)
        != _numeric_bindings(value, clinical_only=True)
    ):
        issues.append(
            ValidationIssue("SUMMARY_NUMERIC_BINDING_MISMATCH", atom_id=atom_id)
        )

    _append_polarity_issues(issues, value, source, atom_id)

    source_levels = set(extract_recommendation_levels(source))
    for level in extract_recommendation_levels(value):
        if level not in source_levels:
            issues.append(
                ValidationIssue(
                    "SUMMARY_NEW_RECOMMENDATION_LEVEL",
                    atom_id=atom_id,
                    detail=level,
                )
            )
    return issues


def _response_groups(
    response: SemanticBatchResponse,
) -> tuple[
    tuple[str, Sequence[SemanticChunkDecision | SemanticRangeDecision]], ...
]:
    return (
        ("chunk", response.chunks),
        ("excluded", response.excluded_ranges),
        ("review", response.review_ranges),
    )


def _expand_range(
    item: SemanticChunkDecision | SemanticRangeDecision,
    positions: Mapping[str, int],
    ordered: Sequence[SourceAtom],
    issues: list[ValidationIssue],
) -> list[SourceAtom] | None:
    missing = [
        atom_id
        for atom_id in (item.start_atom_id, item.end_atom_id)
        if atom_id not in positions
    ]
    if missing:
        for atom_id in dict.fromkeys(missing):
            issues.append(ValidationIssue("UNKNOWN_ATOM_ID", atom_id=atom_id))
        return None
    start = positions[item.start_atom_id]
    end = positions[item.end_atom_id]
    if start > end:
        issues.append(
            ValidationIssue(
                "INVALID_ATOM_RANGE_ORDER",
                atom_id=item.start_atom_id,
                detail=f"{item.start_atom_id}..{item.end_atom_id}",
            )
        )
        return None
    return list(ordered[start : end + 1])


def validate_semantic_response(
    batch: SemanticBatch,
    response: SemanticBatchResponse,
    atom_index: dict[str, SourceAtom],
    allowed_tags: Collection[str],
    *,
    summary_max_chars: int = 120,
    review_max_chars: int = 500,
) -> list[ValidationIssue]:
    """在不信任模型所给边界的前提下验证一个语义响应。"""

    issues: list[ValidationIssue] = []
    if not isinstance(response, SemanticBatchResponse):
        return [ValidationIssue("INVALID_RESPONSE_SCHEMA")]
    if summary_max_chars < 1 or review_max_chars < 1:
        raise ValueError("validation length limits must be positive")

    ordered = _ordered_atoms(atom_index)
    positions = {atom.atom_id: index for index, atom in enumerate(ordered)}
    core_ids = set(batch.core_atom_ids)
    context_list = batch.context_before_ids + batch.context_after_ids
    context_ids = set(context_list)
    if len(core_ids) != len(batch.core_atom_ids):
        issues.append(ValidationIssue("DUPLICATE_CORE_ATOM_ID"))
    if len(context_ids) != len(context_list):
        issues.append(ValidationIssue("DUPLICATE_CONTEXT_ATOM_ID"))
    for atom_id in sorted(core_ids & context_ids):
        issues.append(ValidationIssue("INVALID_BATCH_OWNERSHIP", atom_id=atom_id))
    for atom_id in batch.core_atom_ids + context_list:
        if atom_id not in atom_index:
            issues.append(ValidationIssue("UNKNOWN_BATCH_ATOM_ID", atom_id=atom_id))
    core_positions = [positions[atom_id] for atom_id in batch.core_atom_ids if atom_id in positions]
    if core_positions != sorted(core_positions):
        issues.append(ValidationIssue("BATCH_CORE_OUT_OF_SOURCE_ORDER"))
    allowed_batch_ids = core_ids | context_ids
    disposition_counts: Counter[str] = Counter()
    expanded_chunks: list[tuple[SemanticChunkDecision, list[SourceAtom]]] = []

    for _, group in _response_groups(response):
        previous_start = -1
        for item in group:
            atoms = _expand_range(item, positions, ordered, issues)
            if atoms is None:
                continue
            start_position = positions[atoms[0].atom_id]
            if start_position < previous_start:
                issues.append(
                    ValidationIssue(
                        "RANGES_OUT_OF_SOURCE_ORDER", atom_id=item.start_atom_id
                    )
                )
            previous_start = start_position

            outside_batch = [
                atom.atom_id for atom in atoms if atom.atom_id not in allowed_batch_ids
            ]
            outside_core = [
                atom.atom_id for atom in atoms if atom.atom_id not in core_ids
            ]
            if outside_batch:
                issues.append(
                    ValidationIssue(
                        "ATOM_OUTSIDE_BATCH",
                        atom_id=outside_batch[0],
                        detail=",".join(outside_batch),
                    )
                )
            if outside_core:
                issues.append(
                    ValidationIssue(
                        "ATOM_OUTSIDE_CORE_OWNERSHIP",
                        atom_id=outside_core[0],
                        detail=",".join(outside_core),
                    )
                )

            section_keys = {
                (atom.section_id, tuple(atom.section_path)) for atom in atoms
            }
            if len(section_keys) != 1:
                issues.append(
                    ValidationIssue(
                        "CROSS_SECTION_RANGE",
                        atom_id=item.start_atom_id,
                        detail=f"{item.start_atom_id}..{item.end_atom_id}",
                    )
                )

            range_is_valid = not outside_batch and not outside_core and len(section_keys) == 1
            if range_is_valid:
                disposition_counts.update(atom.atom_id for atom in atoms)
                if isinstance(item, SemanticChunkDecision):
                    expanded_chunks.append((item, atoms))

    for atom_id in batch.core_atom_ids:
        count = disposition_counts[atom_id]
        if count == 0:
            issues.append(ValidationIssue("MISSING_ATOM_DISPOSITION", atom_id=atom_id))
        elif count > 1:
            issues.append(
                ValidationIssue(
                    "OVERLAPPING_ATOM_DISPOSITION",
                    atom_id=atom_id,
                    detail=str(count),
                )
            )

    allowed = frozenset(allowed_tags)
    for chunk, atoms in expanded_chunks:
        if not 1 <= len(chunk.topic_tags) <= 3:
            issues.append(
                ValidationIssue("INVALID_TOPIC_COUNT", atom_id=chunk.start_atom_id)
            )
        if len(chunk.topic_tags) != len(set(chunk.topic_tags)):
            issues.append(
                ValidationIssue("DUPLICATE_TOPIC_TAG", atom_id=chunk.start_atom_id)
            )
        for tag in chunk.topic_tags:
            if tag not in allowed:
                issues.append(
                    ValidationIssue(
                        "INVALID_TOPIC_TAG",
                        atom_id=chunk.start_atom_id,
                        detail=tag,
                    )
                )

        source = _source_for_atoms(atoms)
        issues.extend(
            validate_summary(
                chunk.summary,
                source,
                atom_id=chunk.start_atom_id,
                max_chars=summary_max_chars,
            )
        )
        source_length = atoms[-1].end_char - atoms[0].start_char
        if source_length > review_max_chars:
            issues.append(
                ValidationIssue(
                    "CHUNK_EXCEEDS_REVIEW_MAX_CHARS",
                    atom_id=chunk.start_atom_id,
                    detail=f"{source_length} > {review_max_chars}",
                )
            )
        levels = extract_recommendation_levels(source)
        chunk_type = getattr(chunk.type, "value", chunk.type)
        if chunk_type == "fact" and levels:
            issues.append(
                ValidationIssue(
                    "FACT_CONTAINS_RECOMMENDATION_LEVEL",
                    atom_id=chunk.start_atom_id,
                )
            )
    return issues


def _expected_fields(chunk: FactChunk | RecommendationChunk) -> set[str]:
    common = {
        "chunk_id",
        "source_doc",
        "section_id",
        "section_path",
        "type",
        "topic_tags",
        "summary",
        "content",
    }
    if isinstance(chunk, RecommendationChunk):
        common.add("recommendation_levels")
    return common


def _chunk_payload(chunk: FactChunk | RecommendationChunk) -> tuple[str, ...]:
    return (
        chunk.source_doc,
        chunk.type,
        chunk.section_id,
        chunk.content,
    )


def _audit_identifier(record: object) -> str:
    if isinstance(record, str):
        return record
    if isinstance(record, Mapping):
        return str(record.get("audit_id", record))
    return str(getattr(record, "audit_id", record))


def validate_document_chunks(
    document: SourceDocument,
    chunks: list[FactChunk | RecommendationChunk],
    spans: Mapping[str, tuple[int, int]],
    *,
    allowed_tags: Collection[str] | None = None,
    disposition_atom_ids: Collection[str] | None = None,
    recommendation_covered_atom_ids: Collection[str] | None = None,
    unresolved_audit_ids: Collection[str] | None = None,
    unresolved_audit_records: Collection[object] | None = None,
    summary_max_chars: int = 120,
    review_max_chars: int = 500,
) -> list[ValidationIssue]:
    """应用覆盖整篇文档的原文精确性与质量门禁不变量。"""

    if summary_max_chars < 1 or review_max_chars < 1:
        raise ValueError("validation length limits must be positive")
    issues: list[ValidationIssue] = []
    if allowed_tags is None or not allowed_tags:
        issues.append(
            ValidationIssue("MISSING_VALIDATION_EVIDENCE", detail="allowed_tags")
        )
    if disposition_atom_ids is None:
        issues.append(
            ValidationIssue(
                "MISSING_VALIDATION_EVIDENCE", detail="disposition_atom_ids"
            )
        )
    if recommendation_covered_atom_ids is None:
        issues.append(
            ValidationIssue(
                "MISSING_VALIDATION_EVIDENCE",
                detail="recommendation_covered_atom_ids",
            )
        )
    if unresolved_audit_ids is None and unresolved_audit_records is None:
        issues.append(
            ValidationIssue(
                "MISSING_VALIDATION_EVIDENCE", detail="unresolved_audit_records"
            )
        )
    atoms = sorted(document.atoms, key=lambda atom: (atom.start_char, atom.end_char))
    atom_starts = {atom.start_char for atom in atoms}
    atom_ends = {atom.end_char for atom in atoms}
    allowed = frozenset(allowed_tags) if allowed_tags is not None else None
    seen: set[str] = set()
    stable_id_payloads: dict[str, Sequence[str]] = {}
    previous_start = -1
    previous_end = -1

    for chunk in chunks:
        if chunk.chunk_id in seen:
            issues.append(
                ValidationIssue("DUPLICATE_CHUNK_ID", detail=chunk.chunk_id)
            )
        seen.add(chunk.chunk_id)

        if set(chunk.model_dump(mode="json")) != _expected_fields(chunk):
            issues.append(ValidationIssue("INVALID_CHUNK_FIELDS", detail=chunk.chunk_id))
        if (
            isinstance(chunk, FactChunk)
            and chunk.type != "fact"
            or isinstance(chunk, RecommendationChunk)
            and chunk.type != "recommendation"
        ):
            issues.append(ValidationIssue("INVALID_CHUNK_TYPE", detail=chunk.chunk_id))
        if chunk.source_doc != document.source_doc:
            issues.append(ValidationIssue("SOURCE_DOC_MISMATCH", detail=chunk.chunk_id))
        if not 1 <= len(chunk.topic_tags) <= 3:
            issues.append(ValidationIssue("INVALID_TOPIC_COUNT", detail=chunk.chunk_id))
        if len(chunk.topic_tags) != len(set(chunk.topic_tags)):
            issues.append(ValidationIssue("DUPLICATE_TOPIC_TAG", detail=chunk.chunk_id))
        if allowed is not None:
            for tag in chunk.topic_tags:
                if tag not in allowed:
                    issues.append(
                        ValidationIssue("INVALID_TOPIC_TAG", detail=f"{chunk.chunk_id}: {tag}")
                    )

        span = spans.get(chunk.chunk_id)
        if span is None:
            issues.append(ValidationIssue("MISSING_CHUNK_SPAN", detail=chunk.chunk_id))
            continue
        if (
            not isinstance(span, tuple)
            or len(span) != 2
            or not all(isinstance(value, int) for value in span)
        ):
            issues.append(ValidationIssue("INVALID_CHUNK_SPAN", detail=chunk.chunk_id))
            continue
        start, end = span
        if start < 0 or end <= start or end > len(document.text):
            issues.append(ValidationIssue("INVALID_CHUNK_SPAN", detail=chunk.chunk_id))
            continue
        covered = [
            atom for atom in atoms if atom.start_char >= start and atom.end_char <= end
        ]
        start_atom_id = (
            covered[0].atom_id
            if covered and covered[0].start_char == start
            else None
        )
        if start not in atom_starts or end not in atom_ends:
            issues.append(ValidationIssue("NON_ATOM_BOUNDARY", detail=chunk.chunk_id))
        if document.text[start:end] != chunk.content:
            issues.append(ValidationIssue("NON_EXACT_SOURCE", detail=chunk.chunk_id))
        issues.extend(
            validate_summary(
                chunk.summary,
                chunk.content,
                atom_id=start_atom_id,
                max_chars=summary_max_chars,
            )
        )
        if len(chunk.content) > review_max_chars:
            issues.append(
                ValidationIssue(
                    "CHUNK_EXCEEDS_REVIEW_MAX_CHARS",
                    atom_id=start_atom_id,
                    detail=f"{len(chunk.content)} > {review_max_chars}",
                )
            )
        if isinstance(chunk, RecommendationChunk):
            expected_levels = extract_recommendation_levels(chunk.content)
            if chunk.recommendation_levels != expected_levels:
                issues.append(
                    ValidationIssue(
                        "INVALID_RECOMMENDATION_LEVELS",
                        atom_id=start_atom_id,
                        detail=chunk.chunk_id,
                    )
                )
        elif extract_recommendation_levels(chunk.content):
            issues.append(
                ValidationIssue(
                    "FACT_CONTAINS_RECOMMENDATION_LEVEL",
                    atom_id=start_atom_id,
                    detail=chunk.chunk_id,
                )
            )
        if start < previous_start:
            issues.append(
                ValidationIssue("CHUNKS_OUT_OF_SOURCE_ORDER", detail=chunk.chunk_id)
            )
        if start < previous_end:
            issues.append(ValidationIssue("OVERLAPPING_CHUNK", detail=chunk.chunk_id))
        previous_start = start
        previous_end = max(previous_end, end)

        if not covered or any(
            atom.section_id != chunk.section_id
            or atom.section_path != chunk.section_path
            for atom in covered
        ):
            issues.append(
                ValidationIssue("CHUNK_SECTION_MISMATCH", detail=chunk.chunk_id)
            )

        payload = _chunk_payload(chunk)
        expected_id = make_unique_chunk_id(
            chunk.source_doc,
            chunk.type,
            chunk.section_id,
            chunk.content,
            stable_id_payloads,
        )
        if chunk.chunk_id != expected_id:
            issues.append(ValidationIssue("INVALID_STABLE_ID", detail=chunk.chunk_id))
        stable_id_payloads[expected_id] = payload

    if disposition_atom_ids is not None:
        disposed = set(disposition_atom_ids)
        for atom in atoms:
            if atom.atom_id not in disposed:
                issues.append(
                    ValidationIssue("MISSING_SOURCE_DISPOSITION", atom_id=atom.atom_id)
                )
    if recommendation_covered_atom_ids is not None:
        covered_recommendations = set(recommendation_covered_atom_ids)
        for atom in atoms:
            if atom.recommendation_candidate and atom.atom_id not in covered_recommendations:
                issues.append(
                    ValidationIssue("RECOMMENDATION_NOT_COVERED", atom_id=atom.atom_id)
                )

    unresolved: list[object] = []
    if unresolved_audit_ids is not None:
        unresolved.extend(unresolved_audit_ids)
    if unresolved_audit_records is not None:
        unresolved.extend(unresolved_audit_records)
    for record in unresolved:
        issues.append(
            ValidationIssue("UNRESOLVED_AUDIT_RECORD", detail=_audit_identifier(record))
        )
    return issues
