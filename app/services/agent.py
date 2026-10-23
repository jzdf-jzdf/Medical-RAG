"""Minimal model -> tool -> model agent loop."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol

from app.services.mcp_client import ToolDefinition, ToolOutput
from app.services.verification import (
    compact_citations,
    extract_claims,
    normalize_citations,
    validate_semantic_verification,
    verify_answer,
)


class ModelRunner(Protocol):
    async def generate(
        self,
        prompt: str,
        *,
        conversation_id: str,
        model: str | None = None,
        provider: str | None = None,
    ) -> str: ...


class Toolbox(Protocol):
    async def list_tools(self) -> list[ToolDefinition]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutput: ...


@dataclass(slots=True)
class AgentResult:
    answer: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    degraded_without_evidence: bool = False
    citations: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)


class MedicalAgent:
    def __init__(self, model_runner: ModelRunner, max_tool_rounds: int = 8) -> None:
        self.model_runner = model_runner
        self.max_tool_rounds = max(1, max_tool_rounds)

    async def run(
        self,
        *,
        question: str,
        conversation_id: str,
        history: list[dict[str, str]],
        toolbox: Toolbox,
        model: str,
        provider: str,
        tool_context: dict[str, Any] | None = None,
    ) -> AgentResult:
        tools = await toolbox.list_tools()
        if not tools:
            answer = await self.model_runner.generate(
                _answer_prompt(question, history, [], no_tools=True),
                conversation_id=conversation_id,
                model=model,
                provider=provider,
            )
            return AgentResult(
                answer=_with_unverified_notice(answer), degraded_without_evidence=True
            )

        observations: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        tool_index = {tool.name: tool for tool in tools}

        for _ in range(self.max_tool_rounds):
            raw = await self.model_runner.generate(
                _decision_prompt(question, history, tools, observations, tool_context),
                conversation_id=conversation_id,
                model=model,
                provider=provider,
            )
            decision = _parse_json_object(raw)
            if not decision or decision.get("action") == "final":
                answer = str(decision.get("answer") or raw) if decision else raw
                if _extract_knowledge_evidence(calls)[0]:
                    return await self._complete_evidence_answer(
                        question=question,
                        history=history,
                        calls=calls,
                        conversation_id=conversation_id,
                        model=model,
                        provider=provider,
                    )
                return _finalize_answer(
                    answer,
                    calls,
                    requires_evidence=bool(
                        decision.get("requires_evidence", True) if decision else True
                    ),
                )

            name = str(decision.get("tool") or "")
            arguments = decision.get("arguments")
            if name not in tool_index or not isinstance(arguments, dict):
                observations.append({"tool": name, "is_error": True, "content": "工具名或参数无效"})
                continue

            tool = tool_index[name]
            arguments = _apply_tool_context(tool, arguments, tool_context)
            started = perf_counter()
            try:
                output = await toolbox.call_tool(name, arguments)
            except Exception as exc:
                output = ToolOutput(content={"error": str(exc)}, is_error=True)
            latency_ms = round((perf_counter() - started) * 1000)
            canonical = json.dumps(output.content, ensure_ascii=False, sort_keys=True, default=str)
            record = {
                "server": tool.server,
                "tool": tool.original_name,
                "arguments": arguments,
                "result": output.content,
                "result_hash": hashlib.sha256(canonical.encode()).hexdigest(),
                "latency_ms": latency_ms,
                "status": "failed" if output.is_error else "completed",
                "is_error": output.is_error,
            }
            calls.append(record)
            observations.append({**record, "result": output.for_model()})

        if _extract_knowledge_evidence(calls)[0]:
            return await self._complete_evidence_answer(
                question=question,
                history=history,
                calls=calls,
                conversation_id=conversation_id,
                model=model,
                provider=provider,
            )
        answer = await self.model_runner.generate(
            _answer_prompt(question, history, observations),
            conversation_id=conversation_id,
            model=model,
            provider=provider,
        )
        return _finalize_answer(answer, calls, requires_evidence=True)

    async def _complete_evidence_answer(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
        calls: list[dict[str, Any]],
        conversation_id: str,
        model: str,
        provider: str,
    ) -> AgentResult:
        citations, _ = _extract_knowledge_evidence(calls)
        answer = await self.model_runner.generate(
            _evidence_answer_prompt(question, history, citations),
            conversation_id=conversation_id,
            model=model,
            provider=provider,
        )
        result = _finalize_answer(answer, calls, requires_evidence=True)
        if not result.citations:
            return result
        try:
            raw_verification = await self.model_runner.generate(
                _semantic_verification_prompt(result.answer, result.citations),
                conversation_id=conversation_id,
                model=model,
                provider=provider,
            )
            payload = _parse_json_object(raw_verification)
        except Exception:
            payload = None
        semantic = validate_semantic_verification(result.answer, result.citations, payload)
        issues = sorted(set(result.verification.get("issues", []) + semantic["issues"]))
        result.verification.update(
            {
                "passed": bool(result.verification.get("passed")) and semantic["passed"],
                "issues": issues,
                "semantic_passed": semantic["passed"],
                "claims": semantic["claims"],
                "supported_claims": semantic["supported_claims"],
            }
        )
        result.degraded_without_evidence = not result.verification["passed"]
        return result


def _decision_prompt(
    question: str,
    history: list[dict[str, str]],
    tools: list[ToolDefinition],
    observations: list[dict[str, Any]],
    tool_context: dict[str, Any] | None,
) -> str:
    inventory = [
        {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        for tool in tools
    ]
    return (
        "你正在执行医疗问答工具决策。只输出一个 JSON 对象，不要使用 Markdown。\n"
        '需要调用工具：{"action":"call_tool","tool":"工具名","arguments":{}}\n'
        '无需继续调用：{"action":"final","requires_evidence":true}\n'
        "问候、致谢、界面使用方法等不含医学事实的问题无需检索。\n"
        "仅在无需检索时，可在 final 对象中增加 answer 并将 requires_evidence 设为 false。\n"
        "疾病、诊断、治疗、用药、剂量、数值、风险、指南或临床决策问题必须先调用知识库检索工具。\n"
        "获得检索结果后，回答中每个医学事实句末必须使用 [1]、[2] 等来源编号。\n"
        "只能调用给定工具，不得伪造工具结果。\n"
        f"知识库上下文：{json.dumps(tool_context or {}, ensure_ascii=False)}\n"
        f"对话历史：{json.dumps(history, ensure_ascii=False)}\n"
        f"用户问题：{question}\n"
        f"可用工具：{json.dumps(inventory, ensure_ascii=False)}\n"
        f"已有工具结果：{json.dumps(observations, ensure_ascii=False, default=str)}"
    )


def _answer_prompt(
    question: str,
    history: list[dict[str, str]],
    observations: list[dict[str, Any]],
    *,
    no_tools: bool = False,
) -> str:
    evidence_note = "当前没有可用证据工具。" if no_tools else "请仅依据已有工具结果作答。"
    return (
        f"{evidence_note} 不要输出工具协议或 JSON。证据不足时明确说明。\n"
        f"对话历史：{json.dumps(history, ensure_ascii=False)}\n"
        f"用户问题：{question}\n"
        f"工具结果：{json.dumps(observations, ensure_ascii=False, default=str)}"
    )


def _evidence_answer_prompt(
    question: str,
    history: list[dict[str, str]],
    citations: list[dict[str, Any]],
) -> str:
    sources = [
        {
            "citation": index,
            "title": source.get("title"),
            "heading": source.get("heading"),
            "pages": [source.get("page_start"), source.get("page_end")],
            "text": source.get("text"),
        }
        for index, source in enumerate(citations, start=1)
    ]
    return (
        "你是循证医学问答助手。只能依据给定来源作答；证据不足时明确拒答。\n"
        "来源数组中的 citation 是后端分配的全局唯一编号，必须严格按该编号引用。\n"
        "每个事实性句子末尾必须添加一个或多个来源编号，如 [1] 或 [1][2]。\n"
        "数字、单位、条件、适用人群不得超出来源；不要编造来源编号；不要输出 JSON。\n"
        f"对话历史：{json.dumps(history, ensure_ascii=False)}\n"
        f"用户问题：{question}\n"
        f"来源：{json.dumps(sources, ensure_ascii=False, default=str)}"
    )


def _semantic_verification_prompt(
    answer: str, citations: list[dict[str, Any]]
) -> str:
    claims = [
        {
            "claim_id": item["claim_id"],
            "text": item["text"],
            "citation_ids": item["citation_ids"],
        }
        for item in extract_claims(answer)
        if item["requires_support"] or item["citation_ids"]
    ]
    sources = [
        {"citation_id": index, "text": source.get("text") or ""}
        for index, source in enumerate(citations, start=1)
    ]
    return (
        "你是严格的医学证据核验器。只输出 JSON 对象，不要输出 Markdown。\n"
        "逐条判断陈述是否被它标注的来源直接支持，不得使用常识补全。\n"
        "supported=true 时，evidence 必须至少包含一条来源中的连续逐字原文；"
        "quote 不得改写，citation_id 必须来自该陈述的 citation_ids。\n"
        "输出格式："
        '{"claims":[{"claim_id":1,"supported":true,'
        '"evidence":[{"citation_id":1,"quote":"逐字原文"}],"reason":""}]}\n'
        f"陈述：{json.dumps(claims, ensure_ascii=False)}\n"
        f"来源：{json.dumps(sources, ensure_ascii=False, default=str)}"
    )


def _apply_tool_context(
    tool: ToolDefinition,
    arguments: dict[str, Any],
    tool_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep model-selected retrieval inside the collection chosen by the request."""

    if tool.original_name != "search_medical_knowledge" or not tool_context:
        return arguments
    scoped = dict(arguments)
    if tool_context.get("collection_id"):
        scoped["collection_id"] = tool_context["collection_id"]
    if tool_context.get("top_k"):
        scoped.setdefault("top_k", tool_context["top_k"])
    return scoped


def _finalize_answer(
    answer: str,
    calls: list[dict[str, Any]],
    *,
    requires_evidence: bool,
) -> AgentResult:
    citations, trace = _extract_knowledge_evidence(calls)
    if citations:
        answer = normalize_citations(answer)
        answer, citations = compact_citations(answer, citations)
        verification = verify_answer(answer, citations)
        degraded = not verification["passed"]
        return AgentResult(
            answer=answer,
            tool_calls=calls,
            citations=citations,
            verification=verification,
            trace=trace,
            degraded_without_evidence=degraded,
        )

    degraded = requires_evidence
    if degraded:
        answer = _with_unverified_notice(answer)
    return AgentResult(
        answer=answer,
        tool_calls=calls,
        degraded_without_evidence=degraded,
        verification={
            "passed": not requires_evidence,
            "mode": "not_required" if not requires_evidence else "no_evidence",
            "issues": [] if not requires_evidence else ["missing_sources"],
            "citation_count": 0,
        },
    )


def _extract_knowledge_evidence(
    calls: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    citations: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    seen_chunks: set[str] = set()
    for call in calls:
        if call["is_error"] or call["tool"] not in {
            "search_medical_knowledge",
            "hybrid_search",
        }:
            continue
        payload = call.get("result")
        if not isinstance(payload, dict):
            continue
        items = payload.get("items")
        if not isinstance(items, list):
            continue
        if isinstance(payload.get("trace"), list):
            trace.extend(payload["trace"])
        for item in items:
            if not isinstance(item, dict):
                continue
            chunk_id = str(item.get("chunk_id") or "")
            if chunk_id and chunk_id in seen_chunks:
                continue
            if chunk_id:
                seen_chunks.add(chunk_id)
            citations.append(
                {
                    "document_id": item.get("document_id"),
                    "chunk_id": item.get("chunk_id"),
                    "index_version": item.get("index_version"),
                    "title": item.get("document_title") or item.get("title"),
                    "heading": " › ".join(item.get("heading_path") or []),
                    "page_start": item.get("page_start") or item.get("page"),
                    "page_end": item.get("page_end") or item.get("page"),
                    "text": item.get("content") or item.get("text") or "",
                    "score": item.get("score"),
                    "rank": item.get("rank"),
                }
            )
    return citations, trace


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            return value if isinstance(value, dict) else None
    return None


def _with_unverified_notice(answer: str) -> str:
    notice = "\n\n> 未经文献验证：本轮未获得有效的工具证据，请勿将回答直接用于临床决策。"
    return answer if "未经文献验证" in answer else answer.rstrip() + notice
