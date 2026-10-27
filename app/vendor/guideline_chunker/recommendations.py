"""针对精确原文原子的保守型推荐意见候选检测。"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .models import AtomKind, SourceAtom


NORMATIVE_RE = re.compile(r"(?:推荐|建议|不建议|不推荐|应当|应|不应|可考虑|禁用)")
LEVEL_RE = re.compile(
    r"[（(]\s*([^（）()]{0,40}?(?:级推荐|推荐级别)[^（）()]{0,40}?"
    r"(?:级证据|证据等级)?[^（）()]*)\s*[）)]"
)
GRADE_RE = re.compile(
    r"(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX]+级推荐|推荐级别|[A-DＡ-Ｄ]级证据|证据等级)",
    re.IGNORECASE,
)
RECOMMENDATION_INTRO_RE = re.compile(
    r"^(?:推荐意见|推荐建议|专家建议)[ \t]*[：:]?[ \t]*$"
)
HEADING_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:[（(]\s*(?:\d+(?:[.．]\d+)*|[一二三四五六七八九十百千万]+|[IVX]+)"
    r"\s*[）)]|(?:第\s*)?(?:\d+(?:[.．]\d+)*|[一二三四五六七八九十百千万]+|[IVX]+)"
    r"(?:\s*[.．、:：)）-]|\s+))\s*",
    re.IGNORECASE,
)
RECOMMENDATION_SECTION_TITLES = frozenset(
    {"推荐", "建议", "推荐意见", "推荐建议", "专家建议"}
)


def extract_recommendation_levels(text: str) -> list[str]:
    """按原文顺序返回各不相同的括号内推荐等级。

    绝不改写原文；返回值仅去掉匹配等级两侧的外层括号和空白字符。
    """

    values: list[str] = []
    seen: set[str] = set()
    for match in LEVEL_RE.finditer(text):
        value = match.group(1).strip()
        if value not in seen:
            values.append(value)
            seen.add(value)
    return values


def _is_recommendation_section(section_path: Sequence[str]) -> bool:
    return any(
        HEADING_NUMBER_PREFIX_RE.sub("", title.strip())
        in RECOMMENDATION_SECTION_TITLES
        for title in section_path
    )


def _is_recommendation_intro(text: str) -> bool:
    return RECOMMENDATION_INTRO_RE.fullmatch(text.strip()) is not None


def _is_recommendation_candidate(
    atom: SourceAtom,
    *,
    in_recommendation_block: bool,
) -> bool:
    return (
        atom.recommendation_candidate
        or atom.kind == AtomKind.RECOMMENDATION
        or in_recommendation_block
        or _is_recommendation_section(atom.section_path)
        or NORMATIVE_RE.search(atom.text) is not None
        or GRADE_RE.search(atom.text) is not None
        or bool(extract_recommendation_levels(atom.text))
    )


def mark_recommendation_candidates(atoms: Sequence[SourceAtom]) -> list[SourceAtom]:
    """在不改变原始数据的前提下，标记推荐意见候选的保守超集。

    独立的推荐意见引导语仅在同一章节内将上下文传递给紧随其后的连续列表。
    推荐意见章节的上下文则对该章节内的每个原子持续有效，从而避免不含
    关键词的推荐意见文本被静默遗漏。
    """

    marked: list[SourceAtom] = []
    awaiting_recommendation_list = False
    in_recommendation_list = False
    recommendation_intro_context: tuple[str, tuple[str, ...]] | None = None

    for atom in atoms:
        atom_context = (atom.section_id, tuple(atom.section_path))
        if (
            recommendation_intro_context is not None
            and atom_context != recommendation_intro_context
        ):
            awaiting_recommendation_list = False
            in_recommendation_list = False
            recommendation_intro_context = None

        is_list_item = atom.kind == AtomKind.LIST_ITEM
        inherited_list_context = is_list_item and (
            awaiting_recommendation_list or in_recommendation_list
        )
        candidate = _is_recommendation_candidate(
            atom,
            in_recommendation_block=inherited_list_context,
        )
        marked.append(atom.model_copy(update={"recommendation_candidate": candidate}))

        if is_list_item:
            if inherited_list_context:
                awaiting_recommendation_list = False
                in_recommendation_list = True
            continue

        in_recommendation_list = False
        awaiting_recommendation_list = _is_recommendation_intro(atom.text)
        recommendation_intro_context = (
            atom_context if awaiting_recommendation_list else None
        )

    return marked
