from types import SimpleNamespace

import pytest

from app.api.knowledge_bases import _dedupe_chunk_rows, build_chunk_preview
from app.services.chunking import build_chunks


def test_fixed_chunk_preview_respects_maximum_size() -> None:
    chunks = build_chunk_preview("甲" * 260, "fixed", {"max_chars": 100, "overlap": 10})

    assert len(chunks) == 3
    assert all(len(chunk.content) <= 100 for chunk in chunks)
    assert chunks[1].char_start == 90


def test_heading_chunk_preview_keeps_short_sections() -> None:
    text = "# 第一节\n治疗建议。\n\n## 第二节\n随访建议。"

    chunks = build_chunk_preview(
        text,
        "heading",
        {"max_chars": 200, "overlap": 20, "merge_short_sections": False},
    )

    assert [chunk.content for chunk in chunks] == [
        "# 第一节\n治疗建议。",
        "## 第二节\n随访建议。",
    ]


def test_recursive_chunking_keeps_markdown_table_together_when_possible() -> None:
    # The first natural boundary would fall inside the table. The engine should
    # stop before it and leave the complete table for the next chunk.
    table = "| 指标 | 结果 |\n| --- | --- |\n| 死亡率 | 降低 |\n| 再出血 | 无差异 |"
    text = f"{'前言。' * 24}\n\n{table}\n\n{'结论。' * 20}"

    _, chunks = build_chunks(
        text,
        "recursive",
        {
            "target_chars": 100,
            "max_chars": 160,
            "min_chars": 20,
            "overlap": 0,
            "preserve_tables": True,
        },
    )

    containing_table = [chunk for chunk in chunks if "| 指标 | 结果 |" in chunk.content]
    assert len(containing_table) == 1
    assert "| 再出血 | 无差异 |" in containing_table[0].content


def test_recursive_chunking_keeps_fenced_code_together_when_possible() -> None:
    code = "```python\nresult = analyse(patient)\nprint(result)\n```"
    text = f"{'背景。' * 24}\n\n{code}\n\n{'结论。' * 20}"

    _, chunks = build_chunks(
        text,
        "recursive",
        {
            "target_chars": 100,
            "max_chars": 160,
            "min_chars": 20,
            "overlap": 0,
            "preserve_code_blocks": True,
        },
    )

    containing_code = [chunk for chunk in chunks if "```python" in chunk.content]
    assert len(containing_code) == 1
    assert "print(result)\n```" in containing_code[0].content


def test_unregistered_semantic_engine_is_rejected() -> None:
    with pytest.raises(ValueError, match="不支持的切块引擎"):
        build_chunks("测试文本", "semantic", {})


def test_legacy_chunk_snapshots_are_deduplicated_by_index() -> None:
    rows = [
        SimpleNamespace(chunk_index=1, index_version="initial"),
        SimpleNamespace(chunk_index=2, index_version="initial"),
        SimpleNamespace(chunk_index=1, index_version="rebuilt"),
        SimpleNamespace(chunk_index=2, index_version="rebuilt"),
    ]

    deduped = _dedupe_chunk_rows(rows)

    assert [row.chunk_index for row in deduped] == [1, 2]
    assert [row.index_version for row in deduped] == ["rebuilt", "rebuilt"]
