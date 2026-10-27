"""从扫描得到的 Markdown 块创建精确且有原文依据的原子。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence

from .identifiers import make_section_id
from .models import AtomKind, SourceAtom


# 中文句子边界：一个或多个句号、问号、感叹号、分号。
# 如果句末标点后紧跟右引号，也把右引号归入当前句子，避免下一句以引号开头。
SENTENCE_END_RE = re.compile(r"[。！？；]+(?:[”’\"']+)?")

# 推荐意见的第一轮高召回关键词。这里宁可多标记候选，也不要漏掉推荐内容；
# 后续还会经过 mark_recommendation_candidates() 和语义模型进一步判断。
RECOMMENDATION_RE = re.compile(
    r"推荐|建议|不应|应当|应予|可考虑|不推荐|禁用|不宜|必须|"
    r"[ⅠⅡⅢⅣIVX一二三四]+级推荐|[A-DＡ-Ｄ]级证据",
    re.IGNORECASE,
)

# Markdown 扫描阶段目前只会向原子化阶段传递这四类结构块。
BlockKind = Literal["paragraph", "list_item", "table", "fenced"]


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    """Markdown 结构扫描结果，还不是最终的知识原子。

    该对象只保存结构类型、原文字符范围和章节上下文，不复制或改写正文。

    Attributes:
        kind:
            Markdown 结构类型。普通段落可以继续按句子拆分；列表、表格和
            围栏块会作为不可进一步规则拆分的整体进入原子化阶段。
        start_char:
            block 在完整 Markdown 字符串中的起始位置，包含该位置。
        end_char:
            block 在完整 Markdown 字符串中的结束位置，不包含该位置。
            因而正文可以通过 ``source[start_char:end_char]`` 精确恢复。
        section_path:
            block 所属的标题路径，例如 ``("脑出血治疗", "血压管理")``。
        recommendation_context:
            block 是否位于推荐章节，或是否继承了“推荐意见：”引导语。

    ``frozen=True`` 防止扫描完成后原地修改边界；``slots=True`` 减少大量
    小对象的属性存储开销，并避免意外添加未声明属性。
    """

    kind: BlockKind
    start_char: int
    end_char: int
    section_path: tuple[str, ...]
    recommendation_context: bool = False


def sentence_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """在中文句末标点之后切分一段原文范围。

    返回值是 ``text`` 中的绝对半开区间偏移量。空白字符既不会被改写，
    也不会被猜测，而是严格保持其在句子边界之间的原始归属。

    例如 ``甲；乙。`` 在原文中的范围为 ``[10, 15)`` 时，返回的仍是
    相对于整篇文档的绝对位置，而不是相对于该段落从零开始的位置。
    """

    spans: list[tuple[int, int]] = []

    # cursor 指向当前待生成句子的起点。它从 block 的绝对起点开始。
    cursor = start

    # 每找到一个句末标点，就生成从 cursor 到标点结尾的半开区间。
    for match in SENTENCE_END_RE.finditer(text, start, end):
        spans.append((cursor, match.end()))
        cursor = match.end()

    # 最后一个句末标点之后可能还有没有终止标点的有效文本。
    # 只有该尾部包含非空白字符时才生成一个范围。
    if text[cursor:end].strip():
        spans.append((cursor, end))

    # 过滤完全由空白组成的范围，但不对保留下来的原文执行 strip，
    # 从而继续保持字符偏移与原始 Markdown 完全一致。
    return [
        (span_start, span_end)
        for span_start, span_end in spans
        if text[span_start:span_end].strip()
    ]


def _is_recommendation(text: str, block: MarkdownBlock) -> bool:
    """根据章节语境或关键词，保守判断当前范围是否为推荐候选。"""

    # 章节/引导语上下文的优先级与正文关键词相同，任一满足即可标记。
    return block.recommendation_context or RECOMMENDATION_RE.search(text) is not None


def atomize_blocks(
    source: str, source_doc: str, blocks: Sequence[MarkdownBlock]
) -> list[SourceAtom]:
    """在不改变原文的前提下，将 Markdown block 转换为 SourceAtom。

    转换分为两阶段：

    1. 根据 block 类型确定原子范围和候选类型；
    2. 按原文位置排序后生成稳定的 ``A000001`` 形式编号。

    普通段落按照中文句末标点继续拆分；列表项、表格和围栏块保持完整。
    """

    # 暂存结构依次为：
    # (原子类型, 起始字符, 结束字符, 章节路径, 是否为推荐候选)。
    # 先暂存再统一排序，确保 atom_id 只由原文位置决定。
    pending: list[tuple[AtomKind, int, int, tuple[str, ...], bool]] = []

    for block in blocks:
        # 普通段落允许按中文句末标点进一步细化。
        if block.kind == "paragraph":
            ranges = sentence_spans(source, block.start_char, block.end_char)
        else:
            # 列表、表格和围栏块包含结构信息，整体保留可以避免结构被破坏。
            ranges = [(block.start_char, block.end_char)]

        for start_char, end_char in ranges:
            # atom_text 永远直接切自完整源字符串，不接受重写后的文本。
            atom_text = source[start_char:end_char]
            recommendation_candidate = _is_recommendation(atom_text, block)

            # 结构类型优先于推荐候选语义：
            # 推荐列表项仍保持 LIST_ITEM，推荐属性单独记录在
            # recommendation_candidate 中；普通段落中的推荐句才标为
            # RECOMMENDATION。
            if block.kind == "table":
                atom_kind = AtomKind.TABLE
            elif block.kind == "list_item":
                atom_kind = AtomKind.LIST_ITEM
            elif recommendation_candidate:
                atom_kind = AtomKind.RECOMMENDATION
            else:
                atom_kind = AtomKind.SENTENCE
            pending.append(
                (
                    atom_kind,
                    start_char,
                    end_char,
                    block.section_path,
                    recommendation_candidate,
                )
            )

    # 理论上 Markdown 扫描器已经按原文返回 block，但这里再次排序，
    # 使 atom 编号不依赖调用方传入 blocks 的偶然顺序。
    pending.sort(key=lambda item: (item[1], item[2]))

    # 按原文顺序从 1 开始生成固定宽度 ID。
    # section_id 由文档名和标题路径确定，因此同一文档结构下结果稳定。
    atoms = [
        SourceAtom(
            atom_id=f"A{index:06d}",
            kind=kind,
            start_char=start_char,
            end_char=end_char,
            text=source[start_char:end_char],
            section_id=make_section_id(source_doc, list(section_path)),
            section_path=list(section_path),
            recommendation_candidate=recommendation_candidate,
        )
        for index, (
            kind,
            start_char,
            end_char,
            section_path,
            recommendation_candidate,
        ) in enumerate(pending, start=1)
    ]

    # 在把结果交给后续批次规划和模型调用前，最后确认每个 atom 都能
    # 精确回切原文，并且相邻 atom 没有发生范围重叠。
    assert_exact_atoms(source, atoms)
    return atoms


def assert_exact_atoms(source: str, atoms: Sequence[SourceAtom]) -> None:
    """验证 atom 的文本依据和区间顺序。

    允许 atom 之间存在间隙，例如标题和空行通常不会成为 atom；但不允许
    atom 文本脱离原文、顺序倒置或字符范围相互重叠。
    """

    # -1 使第一个合法的非负起点必然能够通过顺序检查。
    previous_end = -1
    for atom in atoms:
        # SourceAtom.text 必须与相同字符区间的原文逐字符一致。
        if source[atom.start_char : atom.end_char] != atom.text:
            raise ValueError(f"atom {atom.atom_id} is not an exact source slice")

        # 当前起点小于前一个终点，说明两个半开区间发生了重叠，
        # 或调用方没有按原文顺序提供 atom。
        if atom.start_char < previous_end:
            raise ValueError(f"atom {atom.atom_id} overlaps its predecessor")

        previous_end = atom.end_char
