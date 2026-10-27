"""用于原文范围语义决策、带版本且不含原文内容的提示词。"""

from __future__ import annotations

import json

from .models import SemanticBatch, SemanticBatchResponse, SourceAtom


PROMPT_VERSION = "chunk_prompt_v3"
RESPONSE_SCHEMA_VERSION = "semantic_response_v2"

SYSTEM_PROMPT = """你是医学指南语义切分器。只依据输入原文判断，不得使用外部知识。
只能返回JSON对象（JSON only），不得返回Markdown或其他文字；不得生成content；不得改写、补写或拼接原文。
每个核心原文单元必须且只能进入chunks、excluded_ranges或review_ranges之一，必须恰好处置一次；只读上下文不得被处置。
topic_tags只能从allowed_topic_tags白名单选择1至3个。
顶层必须且只能包含chunks、excluded_ranges、review_ranges三个数组，即使为空也必须返回。
chunks元素必须且只能包含start_atom_id、end_atom_id、type、topic_tags、summary、decision_reason；
type只能是fact或recommendation。
excluded_ranges和review_ranges元素必须且只能包含start_atom_id、end_atom_id、reason_code、decision_reason。
所有atom_id必须来自core_atom_ids；禁止输出JSON schema之外的额外字段。
优先生成短而独立的知识块：一个范围只表达一个可独立出题的临床事实或推荐，避免跨越多个主题或大段合并。
summary应忠实保留该范围内的临床数值、单位、比较方向、推荐强度和否定信息；文献引用编号、列表序号、出版年份、作者邮编无需写入summary。
只有纯标题、作者单位、参考文献等完全不含临床信息的范围才能排除；只要含有可用于诊疗或出题的内容，就必须进入chunks或review_ranges。
提交前逐个核对core_atom_ids：每个核心原子恰好出现一次，不遗漏、不重叠；无法可靠判断时放入review_ranges。
只返回结构化结论，不要输出隐藏推理过程、思维链或分析草稿。
"""


def _batch_role(batch: SemanticBatch, atom_id: str) -> str:
    if atom_id in batch.core_atom_ids:
        return "core"
    if atom_id in batch.context_before_ids:
        return "context_before_read_only"
    if atom_id in batch.context_after_ids:
        return "context_after_read_only"
    return "not_in_batch"


def build_user_prompt(
    batch: SemanticBatch,
    atoms: list[SourceAtom],
    tags: tuple[str, ...],
) -> str:
    """将且仅将提供的决策上下文序列化到用户提示词中。"""

    serialized_atoms = []
    for atom in atoms:
        item = atom.model_dump(mode="json")
        item["batch_role"] = _batch_role(batch, atom.atom_id)
        serialized_atoms.append(item)

    payload = {
        "prompt_version": PROMPT_VERSION,
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "required_output_keys": ["chunks", "excluded_ranges", "review_ranges"],
        "core_atom_ids": batch.core_atom_ids,
        "context_before_ids": batch.context_before_ids,
        "context_after_ids": batch.context_after_ids,
        "context_atoms_are_read_only": True,
        "allowed_topic_tags": list(tags),
        "response_json_schema": SemanticBatchResponse.model_json_schema(),
        "response_json_example_do_not_copy_values": {
            "chunks": [
                {
                    "start_atom_id": "<CORE_ATOM_ID>",
                    "end_atom_id": "<CORE_ATOM_ID>",
                    "type": "fact",
                    "topic_tags": ["<ONE_TO_THREE_ALLOWED_TOPIC_TAGS>"],
                    "summary": "<ONE_SENTENCE_SUMMARY>",
                    "decision_reason": "<SHORT_REASON>",
                }
            ],
            "excluded_ranges": [
                {
                    "start_atom_id": "<CORE_ATOM_ID>",
                    "end_atom_id": "<CORE_ATOM_ID>",
                    "reason_code": "<STABLE_REASON_CODE>",
                    "decision_reason": "<SHORT_REASON>",
                }
            ],
            "review_ranges": [],
        },
        "atoms": serialized_atoms,
    }
    return "请按给定JSON结构返回判断结果：\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
