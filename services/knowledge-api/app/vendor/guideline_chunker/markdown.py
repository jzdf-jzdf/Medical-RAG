"""规范化 Markdown 指南的预检与精确偏移解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .atoms import MarkdownBlock, atomize_blocks
from .models import SourceDocument


HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
HEADING_CANDIDATE_RE = re.compile(r"^(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
LIST_ITEM_RE = re.compile(r"^[ \t]{0,3}(?:(?:[-+*])[ \t]+|(?:\d{1,9})[.)][ \t]+)")
FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
TABLE_DELIMITER_CELL_RE = re.compile(r"^:?-{3,}:?$")
CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

EXCLUDED_HEADINGS = {"参考文献", "作者", "作者单位", "致谢", "利益冲突"}
RECOMMENDATION_HEADING_RE = re.compile(r"推荐意见|推荐建议|专家建议")
RECOMMENDATION_INTRO_RE = re.compile(
    r"^(?:推荐意见|推荐建议|专家建议)[ \t]*[：:]?[ \t]*$"
)


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    code: str
    message: str
    line: int | None = None
    column: int | None = None


@dataclass(frozen=True, slots=True)
class PreflightReport:
    path: Path
    errors: tuple[PreflightIssue, ...] = ()
    warnings: tuple[PreflightIssue, ...] = ()

    @property
    def error_codes(self) -> list[str]:
        return [issue.code for issue in self.errors]

    @property
    def warning_codes(self) -> list[str]:
        return [issue.code for issue in self.warnings]


class InputValidationError(ValueError):
    """当严重的预检问题导致解析不安全时抛出。"""

    def __init__(self, errors: Sequence[PreflightIssue]):
        self.errors = tuple(errors)
        details = ", ".join(
            f"{issue.code} (line {issue.line})" if issue.line else issue.code
            for issue in self.errors
        )
        super().__init__(f"Markdown preflight failed: {details}")


@dataclass(frozen=True, slots=True)
class _SourceLine:
    number: int
    start: int
    content_end: int
    end: int
    content: str


def _decode_utf8(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _source_lines(text: str) -> list[_SourceLine]:
    lines: list[_SourceLine] = []
    cursor = 0
    for number, raw_line in enumerate(text.splitlines(keepends=True), start=1):
        if raw_line.endswith("\r\n"):
            content = raw_line[:-2]
        elif raw_line.endswith(("\n", "\r")):
            content = raw_line[:-1]
        else:
            content = raw_line
        content_end = cursor + len(content)
        lines.append(_SourceLine(number, cursor, content_end, cursor + len(raw_line), content))
        cursor += len(raw_line)
    if text and not lines:
        lines.append(_SourceLine(1, 0, len(text), len(text), text))
    return lines


def _heading_parts(line: str) -> tuple[int, str] | None:
    match = HEADING_RE.fullmatch(line)
    if match is None:
        return None
    title = re.sub(r"[ \t]+#+[ \t]*$", "", match.group(2)).strip()
    if not title:
        return None
    return len(match.group(1)), title


def _is_excluded_heading(title: str) -> bool:
    normalized = title.strip().rstrip("：:")
    if normalized in EXCLUDED_HEADINGS:
        return True
    return normalized.startswith("参考文献（") or normalized.lower().startswith(
        "参考文献 ("
    )


def _fence_closes(content: str, marker: str) -> bool:
    stripped = content.lstrip(" \t")
    if not stripped.startswith(marker[0] * len(marker)):
        return False
    run_length = len(stripped) - len(stripped.lstrip(marker[0]))
    return run_length >= len(marker) and not stripped[run_length:].strip()


def preflight_markdown(path: Path) -> PreflightReport:
    """验证 Markdown 结构，并区分阻断性错误与警告。"""

    errors: list[PreflightIssue] = []
    warnings: list[PreflightIssue] = []
    if path.suffix.lower() != ".md":
        errors.append(
            PreflightIssue(
                "INVALID_FILE_EXTENSION",
                "input path must use the .md extension",
            )
        )
    try:
        text = _decode_utf8(path)
    except UnicodeDecodeError as exc:
        errors.append(
            PreflightIssue(
                "INVALID_UTF8",
                "input must be valid UTF-8",
                line=None,
                column=exc.start + 1,
            )
        )
        return PreflightReport(path=path, errors=tuple(errors))

    if text.startswith("\ufeff"):
        errors.append(
            PreflightIssue(
                "UTF8_BOM",
                "UTF-8 BOM is not allowed in normalized Markdown",
                line=1,
                column=1,
            )
        )

    lines = _source_lines(text)
    for match in CONTROL_CHARACTER_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        line_start = text.rfind("\n", 0, match.start()) + 1
        errors.append(
            PreflightIssue(
                "CONTROL_CHARACTER",
                "disallowed control character in source",
                line=line,
                column=match.start() - line_start + 1,
            )
        )

    heading_stack: list[str] = []
    seen_paths: set[tuple[str, ...]] = set()
    previous_level = 0
    open_fence: tuple[str, int] | None = None
    for source_line in lines:
        fence_match = FENCE_RE.match(source_line.content)
        if open_fence is not None:
            marker, _ = open_fence
            if _fence_closes(source_line.content, marker):
                open_fence = None
            continue
        if fence_match is not None:
            open_fence = (fence_match.group(1), source_line.number)
            continue

        candidate = HEADING_CANDIDATE_RE.fullmatch(source_line.content)
        if candidate is None:
            if re.match(r"^#{1,6}\S", source_line.content):
                errors.append(
                    PreflightIssue(
                        "INVALID_ATX_HEADING",
                        "ATX headings require whitespace after the hash marker",
                        source_line.number,
                        1,
                    )
                )
            continue

        level = len(candidate.group(1))
        raw_title = candidate.group(2) or ""
        title = re.sub(r"[ \t]+#+[ \t]*$", "", raw_title).strip()
        if not title:
            errors.append(
                PreflightIssue(
                    "EMPTY_HEADING",
                    "heading must contain a title",
                    source_line.number,
                    level + 1,
                )
            )
            continue
        if level > previous_level + 1:
            warnings.append(
                PreflightIssue(
                    "HEADING_LEVEL_JUMP",
                    f"heading level jumps from {previous_level or 0} to {level}",
                    source_line.number,
                    1,
                )
            )
        previous_level = level
        heading_stack = heading_stack[: level - 1]
        heading_stack.append(title)
        path_key = tuple(heading_stack)
        if path_key in seen_paths:
            warnings.append(
                PreflightIssue(
                    "DUPLICATE_HEADING_PATH",
                    "heading path is repeated",
                    source_line.number,
                    1,
                )
            )
        seen_paths.add(path_key)

    if open_fence is not None:
        errors.append(
            PreflightIssue(
                "UNCLOSED_FENCE",
                "fenced block is not closed",
                open_fence[1],
                1,
            )
        )
    return PreflightReport(path=path, errors=tuple(errors), warnings=tuple(warnings))


def _is_blank(line: _SourceLine) -> bool:
    return not line.content.strip()


def _indent_width(line: str) -> int:
    leading = line[: len(line) - len(line.lstrip(" \t"))]
    return len(leading.expandtabs(4))


def _table_cells(line: str) -> list[str]:
    value = line.strip()
    cells: list[str] = []
    cell_start = 0
    backslash_count = 0
    for index, character in enumerate(value):
        if character == "|" and backslash_count % 2 == 0:
            cells.append(value[cell_start:index].strip())
            cell_start = index + 1
        backslash_count = backslash_count + 1 if character == "\\" else 0
    cells.append(value[cell_start:].strip())
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return cells


def _has_unescaped_pipe(line: str) -> bool:
    backslash_count = 0
    for character in line:
        if character == "|" and backslash_count % 2 == 0:
            return True
        backslash_count = backslash_count + 1 if character == "\\" else 0
    return False


def _is_table_delimiter(line: str) -> bool:
    if not _has_unescaped_pipe(line):
        return False
    cells = _table_cells(line)
    return bool(cells) and all(TABLE_DELIMITER_CELL_RE.fullmatch(cell) for cell in cells)


def _starts_table(lines: Sequence[_SourceLine], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and _has_unescaped_pipe(lines[index].content)
        and _is_table_delimiter(lines[index + 1].content)
        and len(_table_cells(lines[index].content))
        == len(_table_cells(lines[index + 1].content))
    )


def _section_after_heading(
    current: list[str], level: int, title: str
) -> list[str]:
    updated = current[: level - 1]
    updated.append(title)
    return updated


def scan_markdown_blocks(text: str) -> list[MarkdownBlock]:
    """将原文扫描为带精确半开区间的保留块。"""

    # 先按行建立索引；每个 _SourceLine 都保存原文中的绝对字符位置，
    # 后续生成 block 时才能精确记录 [start_char, end_char)。
    lines = _source_lines(text)
    blocks: list[MarkdownBlock] = []

    # section_path 相当于当前标题栈，例如：
    # ["脑出血的治疗", "血压管理"]。
    section_path: list[str] = []

    # 标记上一段是否为独立的“推荐意见：”引导语。
    # 如果是，紧随其后的列表项也应继承推荐语境。
    recommendation_intro_active = False

    # 这里不能使用简单的 for 循环，因为表格、列表和代码围栏可能一次消费多行。
    index = 0
    while index < len(lines):
        line = lines[index]

        # 标题只更新章节路径，本身不生成正文 block。
        heading = _heading_parts(line.content)
        if heading is not None:
            level, title = heading

            # 遇到“参考文献”等排除章节后停止扫描，后面的内容不参与切块。
            if _is_excluded_heading(title):
                break
            section_path = _section_after_heading(section_path, level, title)

            # 推荐引导语的作用范围不能跨越章节标题。
            recommendation_intro_active = False
            index += 1
            continue

        # 空行是 Markdown 块之间的分隔符，不单独生成 block。
        if _is_blank(line):
            index += 1
            continue

        # 如果当前标题路径中包含“推荐”“建议”等标题，
        # 本章节内生成的 block 都带有推荐语境。
        section_recommendation = any(
            RECOMMENDATION_HEADING_RE.search(part) is not None
            for part in section_path
        )

        # 识别 ``` 或 ~~~ 开始的围栏块，并整体保留到对应的结束标记。
        fence_match = FENCE_RE.match(line.content)
        if fence_match is not None:
            marker = fence_match.group(1)
            end_index = index
            for candidate_index in range(index + 1, len(lines)):
                end_index = candidate_index
                if _fence_closes(lines[candidate_index].content, marker):
                    break

            blocks.append(
                MarkdownBlock(
                    "fenced",
                    line.start,
                    lines[end_index].content_end,
                    tuple(section_path),
                    section_recommendation,
                )
            )

            # 围栏块会中断“推荐意见：”对后续列表的临时传递。
            recommendation_intro_active = False
            index = end_index + 1
            continue

        # 表格从表头开始，连续吸收仍然包含未转义竖线的非空行。
        # 整张表作为一个 block，避免表头和数据行被拆散。
        if _starts_table(lines, index):
            end_index = index + 1
            while (
                end_index + 1 < len(lines)
                and not _is_blank(lines[end_index + 1])
                and _has_unescaped_pipe(lines[end_index + 1].content)
                and _heading_parts(lines[end_index + 1].content) is None
            ):
                end_index += 1

            blocks.append(
                MarkdownBlock(
                    "table",
                    line.start,
                    lines[end_index].content_end,
                    tuple(section_path),
                    section_recommendation,
                )
            )

            # 表格同样会终止推荐引导语的临时列表语境。
            recommendation_intro_active = False
            index = end_index + 1
            continue

        # 列表项可能包含多行正文、缩进子内容或由空行分隔的续行，
        # 因此不能只把当前物理行当成一个列表 block。
        list_match = LIST_ITEM_RE.match(line.content)
        if list_match:
            # item_indent 是列表标记本身的缩进；
            # content_indent 是列表正文开始位置的视觉缩进（Tab 按 4 空格展开）。
            item_indent = _indent_width(line.content)
            content_indent = len(line.content[: list_match.end()].expandtabs(4))
            end_index = index
            candidate_index = index + 1

            while candidate_index < len(lines):
                following = lines[candidate_index]

                # 空行后仍可能存在属于当前列表项的缩进续行。
                if _is_blank(following):
                    continuation_index = candidate_index + 1
                    while continuation_index < len(lines) and _is_blank(
                        lines[continuation_index]
                    ):
                        continuation_index += 1

                    # 文件末尾只有空行，当前列表项在此结束。
                    if continuation_index >= len(lines):
                        break
                    continuation = lines[continuation_index]
                    continuation_list = LIST_ITEM_RE.match(continuation.content)

                    # 同级或更外层的新列表标记代表下一个列表项。
                    if (
                        continuation_list is not None
                        and _indent_width(continuation.content) <= item_indent
                    ):
                        break

                    # 缩进不足说明后续文本已经离开当前列表项。
                    if _indent_width(continuation.content) < content_indent:
                        break

                    # 缩进足够时，把中间空行和续行都纳入当前列表项范围。
                    end_index = continuation_index
                    candidate_index = continuation_index + 1
                    continue

                following_list = LIST_ITEM_RE.match(following.content)

                # 未经空行分隔的同级列表标记同样表示当前列表项结束。
                if (
                    following_list is not None
                    and _indent_width(following.content) <= item_indent
                ):
                    break

                # 标题、围栏块或表格都有独立的扫描规则，不能并入列表项。
                if _heading_parts(following.content) is not None:
                    break
                if FENCE_RE.match(following.content) or _starts_table(
                    lines, candidate_index
                ):
                    break
                end_index = candidate_index
                candidate_index += 1

            blocks.append(
                MarkdownBlock(
                    "list_item",
                    line.start,
                    lines[end_index].content_end,
                    tuple(section_path),
                    section_recommendation or recommendation_intro_active,
                )
            )

            # 整个列表项已被一次性消费，直接跳到它后面的第一行。
            index = end_index + 1
            continue

        # 不能识别为标题、空行、围栏、表格或列表时，按普通段落处理。
        # 连续的普通文本行会合并，直到遇到空行或其他 Markdown 块起点。
        end_index = index
        while end_index + 1 < len(lines):
            following_index = end_index + 1
            following = lines[following_index]
            if _is_blank(following):
                break
            if (
                _heading_parts(following.content) is not None
                or FENCE_RE.match(following.content)
                or LIST_ITEM_RE.match(following.content)
                or _starts_table(lines, following_index)
            ):
                break
            end_index += 1

        block = MarkdownBlock(
            "paragraph",
            line.start,
            lines[end_index].content_end,
            tuple(section_path),
            section_recommendation,
        )
        blocks.append(block)

        # 如果整个段落只是“推荐意见：”一类引导语，
        # 让紧随其后的列表项继承推荐语境。
        paragraph_text = text[block.start_char : block.end_char].strip()
        recommendation_intro_active = (
            RECOMMENDATION_INTRO_RE.fullmatch(paragraph_text) is not None
        )

        # 跳过当前段落已经覆盖的全部物理行。
        index = end_index + 1

    return blocks


def parse_source_document(path: Path) -> SourceDocument:
    """将一个 UTF-8 Markdown 文件解析为精确且互不重叠的原文原子。"""

    report = preflight_markdown(path)
    if report.errors:
        raise InputValidationError(report.errors)
    text = _decode_utf8(path)
    source_doc = path.stem
    blocks = scan_markdown_blocks(text)
    atoms = atomize_blocks(text, source_doc, blocks)
    return SourceDocument(source_doc=source_doc, path=path, text=text, atoms=atoms)
