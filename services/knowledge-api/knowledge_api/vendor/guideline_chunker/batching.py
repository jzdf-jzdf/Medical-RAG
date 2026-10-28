"""面向语义模型、感知章节结构的确定性批次规划。"""

from __future__ import annotations

from collections.abc import Sequence

from .config import BatchingConfig
from .models import AtomKind, SemanticBatch, SourceAtom


def _is_indivisible(atom: SourceAtom) -> bool:
    return (
        atom.kind in {AtomKind.TABLE, AtomKind.RECOMMENDATION}
        or atom.recommendation_candidate
    )


def find_oversized_atoms(
    atoms: list[SourceAtom], config: BatchingConfig
) -> list[SourceAtom]:
    """返回无法放入普通语义批次的不可再分原子。"""

    return [
        atom
        for atom in atoms
        if len(atom.text) > config.max_chars and _is_indivisible(atom)
    ]


def _section_key(atom: SourceAtom) -> tuple[str, tuple[str, ...]]:
    return atom.section_id, tuple(atom.section_path)


def _are_sibling_sections(left: SourceAtom, right: SourceAtom) -> bool:
    left_path = tuple(left.section_path)
    right_path = tuple(right.section_path)
    return (
        _section_key(left) != _section_key(right)
        and len(left_path) >= 2
        and len(right_path) >= 2
        and left_path[:-1] == right_path[:-1]
    )


def _section_end(atoms: Sequence[SourceAtom], start: int) -> int:
    key = _section_key(atoms[start])
    end = start + 1
    while end < len(atoms) and _section_key(atoms[end]) == key:
        end += 1
    return end


def _is_section_start(atoms: Sequence[SourceAtom], index: int) -> bool:
    return index == 0 or _section_key(atoms[index - 1]) != _section_key(atoms[index])


def _choose_core_end(
    atoms: Sequence[SourceAtom], start: int, config: BatchingConfig
) -> int:
    """选择受上限约束的核心范围，仅跨越完整的小型同级章节。"""

    end = start
    char_count = 0
    atom_count = 0
    began_at_section_start = _is_section_start(atoms, start)

    while end < len(atoms):
        current_section_end = _section_end(atoms, end)
        same_as_previous = (
            end == start
            or _section_key(atoms[end - 1]) == _section_key(atoms[end])
        )

        if not same_as_previous:
            if not began_at_section_start or not _are_sibling_sections(
                atoms[end - 1], atoms[end]
            ):
                break
            if (
                char_count >= config.target_chars
                or atom_count >= config.target_atoms
            ):
                break

            next_section = atoms[end:current_section_end]
            section_chars = sum(len(atom.text) for atom in next_section)
            section_atoms = len(next_section)
            if (
                section_chars > config.target_chars
                or section_atoms > config.target_atoms
                or char_count + section_chars > config.max_chars
                or atom_count + section_atoms > config.max_atoms
            ):
                break

            char_count += section_chars
            atom_count += section_atoms
            end = current_section_end
        else:
            atom_chars = len(atoms[end].text)
            if (
                char_count + atom_chars > config.max_chars
                or atom_count + 1 > config.max_atoms
            ):
                break
            char_count += atom_chars
            atom_count += 1
            end += 1

        if char_count >= config.target_chars or atom_count >= config.target_atoms:
            break

    if end == start:
        raise ValueError(f"atom {atoms[start].atom_id} cannot fit within batch limits")
    return end


def _plan_segment(
    atoms: Sequence[SourceAtom], config: BatchingConfig
) -> list[tuple[list[str], list[str], list[str], int]]:
    planned: list[tuple[list[str], list[str], list[str], int]] = []
    cursor = 0
    while cursor < len(atoms):
        end = _choose_core_end(atoms, cursor, config)
        core = atoms[cursor:end]
        before_count = min(config.context_before_atoms, 2)
        after_count = min(config.context_after_atoms, 2)
        before = atoms[max(0, cursor - before_count) : cursor]
        after = atoms[end : end + after_count]
        planned.append(
            (
                [atom.atom_id for atom in core],
                [atom.atom_id for atom in before],
                [atom.atom_id for atom in after],
                sum(len(atom.text) for atom in core),
            )
        )
        cursor = end
    return planned


def plan_batches(
    atoms: list[SourceAtom], config: BatchingConfig
) -> list[SemanticBatch]:
    """规划普通批次，并为每个可批处理原子分配唯一的核心归属批次。

    超大表格和推荐意见原子会被完全省略，以便流水线后续阶段将
    :func:`find_oversized_atoms` 的结果转换为审核记录。其他超大原子说明
    上游原子化未能生成可批处理单元，因此会被拒绝，而不是静默突破上限。
    """

    oversized = {id(atom) for atom in find_oversized_atoms(atoms, config)}
    for atom in atoms:
        if len(atom.text) > config.max_chars and id(atom) not in oversized:
            raise ValueError(f"atom {atom.atom_id} cannot fit within batch limits")

    raw_batches: list[tuple[list[str], list[str], list[str], int]] = []
    segment: list[SourceAtom] = []
    for atom in atoms:
        if id(atom) in oversized:
            if segment:
                raw_batches.extend(_plan_segment(segment, config))
                segment = []
            continue
        segment.append(atom)
    if segment:
        raw_batches.extend(_plan_segment(segment, config))

    return [
        SemanticBatch(
            batch_id=f"BATCH_{index:04d}",
            core_atom_ids=core_ids,
            context_before_ids=before_ids,
            context_after_ids=after_ids,
            core_char_count=core_chars,
        )
        for index, (core_ids, before_ids, after_ids, core_chars) in enumerate(
            raw_batches, start=1
        )
    ]
