"""用于章节和知识块的确定性内容寻址标识符。"""

import hashlib
from collections.abc import Mapping, Sequence


DEFAULT_DIGEST_LENGTH = 10


def _digest(parts: Sequence[str], length: int = DEFAULT_DIGEST_LENGTH) -> str:
    """返回规范有序载荷对应的大写 SHA-256 前缀。"""

    if not 1 <= length <= 64:
        raise ValueError("digest length must be between 1 and 64")
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length].upper()


def make_section_id(source_doc: str, section_path: list[str]) -> str:
    """构建文档章节的稳定标识符。"""

    if not section_path:
        return "SEC_ROOT"
    return f"SEC_{_digest([source_doc, *section_path])}"


def make_unique_section_id(
    source_doc: str,
    section_path: list[str],
    existing_payloads: Mapping[str, Sequence[str]],
) -> str:
    """构建可处理冲突的章节 ID，同时保留共享的根 ID。"""

    if not section_path:
        return "SEC_ROOT"
    return make_unique_identifier(
        "SEC", (source_doc, *section_path), existing_payloads
    )


def make_chunk_id(
    source_doc: str, chunk_type: str, section_id: str, content: str
) -> str:
    """为内容与原文精确一致的知识块构建稳定标识符。"""

    prefix = "FACT" if chunk_type == "fact" else "REC"
    return f"{prefix}_{_digest([source_doc, chunk_type, section_id, content])}"


def make_unique_identifier(
    prefix: str,
    parts: Sequence[str],
    existing_payloads: Mapping[str, Sequence[str]],
    *,
    initial_length: int = DEFAULT_DIGEST_LENGTH,
) -> str:
    """返回确定性 ID，仅在发生真实的截断冲突时延长。

    ``existing_payloads`` 将已签发的 ID 映射到其规范载荷组成部分。
    同一载荷复用 ID 时结果稳定；若某 ID 已被不同载荷占用，则逐个增加
    摘要字符，直到能够无歧义地区分。
    """

    canonical_parts = tuple(parts)
    for length in range(initial_length, 65):
        candidate = f"{prefix}_{_digest(canonical_parts, length)}"
        incumbent = existing_payloads.get(candidate)
        if incumbent is None or tuple(incumbent) == canonical_parts:
            return candidate
    raise RuntimeError("SHA-256 collision could not be disambiguated")


def make_unique_chunk_id(
    source_doc: str,
    chunk_type: str,
    section_id: str,
    content: str,
    existing_payloads: Mapping[str, Sequence[str]],
) -> str:
    """供组装阶段使用、可处理冲突的 :func:`make_chunk_id` 对应函数。"""

    prefix = "FACT" if chunk_type == "fact" else "REC"
    return make_unique_identifier(
        prefix,
        (source_doc, chunk_type, section_id, content),
        existing_payloads,
    )
