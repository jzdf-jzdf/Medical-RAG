"""Minimal model -> tool -> model agent loop."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol

from app.services.mcp_client import ToolDefinition, ToolOutput
from app.services.verification import compact_citations, normalize_citations, verify_answer


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
    def __init__(self, model_runner: ModelRunner, max_tool_rounds: int = 4) -> None:
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

        answer = await self.model_runner.generate(
            _answer_prompt(question, history, observations),
            conversation_id=conversation_id,
            model=model,
            provider=provider,
        )
        return _finalize_answer(answer, calls, requires_evidence=True)


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
        '无需继续调用：{"action":"final","requires_evidence":false,"answer":"最终回答"}\n'
        "问候、致谢、界面使用方法等不含医学事实的问题无需检索。\n"
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
        "每个事实性句子末尾必须添加一个或多个来源编号，如 [1] 或 [1][2]。\n"
        "数字、单位、条件、适用人群不得超出来源；不要编造来源编号；不要输出 JSON。\n"
        f"对话历史：{json.dumps(history, ensure_ascii=False)}\n"
        f"用户问题：{question}\n"
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
        if degraded:
            answer = _with_verification_notice(answer, verification)
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


def _with_verification_notice(answer: str, verification: dict[str, Any]) -> str:
    issues = "、".join(verification.get("issues") or ["引用核验未通过"])
    notice = f"\n\n> 引用核验未通过（{issues}）：请复核原始证据后再用于临床决策。"
    return answer if "引用核验未通过" in answer else answer.rstrip() + notice
