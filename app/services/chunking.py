"""Validated, deterministic Markdown chunking engines shared by preview and build."""

import itertools
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChunkingConfig(BaseModel):
    """Serializable recipe persisted with a registered chunking method."""

    model_config = ConfigDict(extra="forbid")

    target_chars: int = Field(default=800, ge=100, le=10_000)
    max_chars: int = Field(default=1200, ge=100, le=20_000)
    min_chars: int = Field(default=200, ge=0, le=5_000)
    overlap: int = Field(default=120, ge=0, le=5_000)
    max_chunks: int = Field(default=2000, ge=1, le=10_000)
    heading_levels: list[int] = Field(default_factory=lambda: [1, 2, 3, 4])
    merge_short_sections: bool = True
    prepend_heading_path: bool = True
    preserve_tables: bool = True
    preserve_code_blocks: bool = True

    @model_validator(mode="after")
    def validate_ranges(self) -> "ChunkingConfig":
        if self.min_chars > self.target_chars:
            raise ValueError("最小字符数不能大于目标字符数")
        if self.target_chars > self.max_chars:
            raise ValueError("目标字符数不能大于最大字符数")
        if self.overlap >= self.max_chars / 2:
            raise ValueError("重叠字符数必须小于最大字符数的一半")
        if not self.heading_levels or any(level < 1 or level > 6 for level in self.heading_levels):
            raise ValueError("标题层级必须在 H1 到 H6 之间")
        self.heading_levels = sorted(set(self.heading_levels))
        return self


@dataclass(frozen=True)
class ChunkResult:
    index: int
    content: str
    embedding_text: str
    char_start: int
    char_end: int
    heading_path: list[str]


SUPPORTED_ENGINES = {
    "heading": "Markdown 标题分块",
    "recursive": "递归边界分块",
    "fixed": "固定长度分块",
}


def validate_chunking_config(strategy: str, raw: dict) -> ChunkingConfig:
    """Normalize legacy recipes and reject unsupported engines or unsafe ranges."""

    if strategy not in SUPPORTED_ENGINES:
        raise ValueError("不支持的切块引擎")
    legacy = dict(raw or {})
    if "target_chars" not in legacy and "max_chars" in legacy:
        legacy["target_chars"] = min(int(legacy["max_chars"]), 800)
    if "min_chars" not in legacy:
        legacy["min_chars"] = min(200, int(legacy.get("target_chars", 800)))
    return ChunkingConfig.model_validate(legacy)


def _embedding_text(content: str, heading_path: list[str], config: ChunkingConfig) -> str:
    """Optionally enrich vector input without changing the text shown to readers."""

    if not config.prepend_heading_path or not heading_path:
        return content
    return f"{' > '.join(heading_path)}\n\n{content}"


def _boundary_before(window: str, minimum: int) -> int:
    """Choose the strongest natural boundary at or after the minimum size."""

    candidates = [
        window.rfind("\n\n"),
        window.rfind("。"),
        window.rfind("；"),
        window.rfind("？"),
        window.rfind("！"),
        window.rfind("\n"),
    ]
    boundary = max(candidates)
    return boundary + 1 if boundary >= minimum else len(window)


def _protected_ranges(text: str, config: ChunkingConfig) -> list[tuple[int, int]]:
    """Locate Markdown structures that should not be split when they fit in one chunk."""

    ranges: list[tuple[int, int]] = []
    if config.preserve_code_blocks:
        # Both backtick and tilde fences are valid Markdown. The closing fence is
        # intentionally anchored at line start so inline code is never captured.
        fence_pattern = re.compile(r"(?ms)^(?:```|~~~).*?^(?:```|~~~)[ \t]*$")
        ranges.extend((match.start(), match.end()) for match in fence_pattern.finditer(text))

    if config.preserve_tables:
        lines = list(re.finditer(r"(?m)^.*(?:\n|$)", text))
        index = 0
        separator = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
        while index + 1 < len(lines):
            header = lines[index].group(0).rstrip("\n")
            delimiter = lines[index + 1].group(0).rstrip("\n")
            if "|" not in header or not separator.match(delimiter):
                index += 1
                continue
            end_index = index + 2
            while end_index < len(lines) and "|" in lines[end_index].group(0):
                end_index += 1
            ranges.append((lines[index].start(), lines[end_index - 1].end()))
            index = end_index

    return sorted(ranges)


def _adjust_for_protected_range(
    start: int,
    end: int,
    hard_end: int,
    protected: list[tuple[int, int]],
    minimum: int,
) -> int:
    """Move a boundary before or after an atomic Markdown block when possible."""

    for protected_start, protected_end in protected:
        if not protected_start < end < protected_end:
            continue
        if protected_end <= hard_end:
            return protected_end
        if protected_start - start >= minimum:
            return protected_start
    return end


def _split_text(
    text: str,
    config: ChunkingConfig,
    *,
    heading_path: list[str] | None = None,
    base_offset: int = 0,
    prefer_boundaries: bool = True,
) -> list[ChunkResult]:
    """Split one text region while preserving source offsets and configured overlap."""

    results: list[ChunkResult] = []
    path = heading_path or []
    protected = _protected_ranges(text, config)
    start = 0
    while start < len(text) and len(results) < config.max_chunks:
        desired_end = min(len(text), start + config.target_chars)
        hard_end = min(len(text), start + config.max_chars)
        end = hard_end
        if prefer_boundaries and hard_end < len(text):
            window = text[start:hard_end]
            preferred = _boundary_before(window[: desired_end - start], config.min_chars)
            if preferred < desired_end - start:
                tail = window[preferred:]
                extended = _boundary_before(tail, 0)
                end = start + min(preferred + extended, len(window))
            else:
                end = start + preferred
        end = _adjust_for_protected_range(
            start,
            end,
            hard_end,
            protected,
            config.min_chars,
        )

        raw = text[start:end]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        content = raw.strip()
        if content:
            results.append(
                ChunkResult(
                    index=len(results) + 1,
                    content=content,
                    embedding_text=_embedding_text(content, path, config),
                    char_start=base_offset + start + leading,
                    char_end=base_offset + start + trailing,
                    heading_path=list(path),
                )
            )
        if end >= len(text):
            break
        start = max(start + 1, end - config.overlap)
    return results


def _heading_sections(text: str, config: ChunkingConfig) -> list[tuple[int, int, list[str]]]:
    """Return section spans plus the heading breadcrumb active at each span."""

    pattern = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
    matches = [
        match for match in pattern.finditer(text) if len(match.group(1)) in config.heading_levels
    ]
    if not matches:
        return []
    boundaries = sorted({0, *(match.start() for match in matches), len(text)})
    heading_stack: list[str] = []
    match_by_start = {match.start(): match for match in matches}
    sections: list[tuple[int, int, list[str]]] = []
    for start, end in itertools.pairwise(boundaries):
        match = match_by_start.get(start)
        if match:
            level = len(match.group(1))
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(match.group(2).strip())
        if text[start:end].strip():
            sections.append((start, end, list(heading_stack)))
    return sections


def build_chunks(
    text: str, strategy: str, raw_config: dict
) -> tuple[ChunkingConfig, list[ChunkResult]]:
    """Execute one registered engine and return normalized config plus stable chunks."""

    config = validate_chunking_config(strategy, raw_config)
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return config, []

    if strategy == "fixed":
        return config, _split_text(normalized, config, prefer_boundaries=False)
    if strategy == "recursive":
        return config, _split_text(normalized, config)

    sections = _heading_sections(normalized, config)
    if not sections:
        return config, _split_text(normalized, config)

    results: list[ChunkResult] = []
    for start, end, path in sections:
        section = normalized[start:end]
        if config.merge_short_sections and len(section.strip()) < config.min_chars and results:
            previous = results.pop()
            combined = f"{previous.content}\n\n{section.strip()}"
            if len(combined) <= config.max_chars:
                results.append(
                    ChunkResult(
                        index=previous.index,
                        content=combined,
                        embedding_text=_embedding_text(combined, previous.heading_path, config),
                        char_start=previous.char_start,
                        char_end=end,
                        heading_path=previous.heading_path,
                    )
                )
                continue
            results.append(previous)

        section_chunks = _split_text(
            section,
            config,
            heading_path=path,
            base_offset=start,
        )
        for chunk in section_chunks:
            results.append(
                ChunkResult(
                    index=len(results) + 1,
                    content=chunk.content,
                    embedding_text=chunk.embedding_text,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    heading_path=chunk.heading_path,
                )
            )
            if len(results) >= config.max_chunks:
                return config, results
    return config, results
