"""Medical agent endpoint."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import enforce_user
from app.db.session import get_db
from app.models import AgentRun, AnswerCitation, Conversation, Message, ModelConfig, ToolCall
from app.schemas.evidence import ChatRequest, ChatResponse
from app.services.agent import AgentResult, MedicalAgent, generate_with_retry, generation_failure
from app.services.crypto import decrypt_secret
from app.services.governance import consume_quota, record_audit
from app.services.harness import HarnessService
from app.services.mcp_client import MCPToolbox

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


def _exception_detail(exc: BaseException, *, limit: int = 800) -> str:
    """Flatten nested ExceptionGroups so the useful leaf cause is not hidden."""

    messages: list[str] = []

    def walk(error: BaseException) -> None:
        if isinstance(error, BaseExceptionGroup):
            for child in error.exceptions:
                walk(child)
            return
        text = str(error).strip()
        messages.append(f"{type(error).__name__}: {text}" if text else type(error).__name__)

    walk(exc)
    return "; ".join(dict.fromkeys(messages))[:limit] or repr(exc)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> ChatResponse:
    enforce_user(request, payload.user_id, "editor")
    try:
        conversation_id = uuid.UUID(payload.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid conversation_id") from exc

    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.user_id != payload.user_id:
        raise HTTPException(status_code=403, detail="Conversation access denied")

    settings = get_settings()
    await consume_quota(db, payload.user_id, settings.daily_request_quota)
    history_rows = list(
        (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(settings.max_history_turns * 2)
            )
        ).all()
    )
    history = [{"role": item.role, "content": item.content} for item in reversed(history_rows)]

    configured_model = await _resolve_model_config(payload.model_config_id, db)
    if configured_model:
        model = configured_model.model
        provider = configured_model.provider
        harness = HarnessService(
            settings,
            base_url=configured_model.base_url,
            api_key=decrypt_secret(configured_model.api_key_encrypted),
            max_tokens=configured_model.max_tokens,
        )
    else:
        profile = settings.model_profiles_json.get(payload.model_profile or "", {})
        model = str(profile.get("model") or settings.model_name)
        provider = str(profile.get("provider") or settings.model_provider)
        harness = HarnessService(settings)

    user_message = Message(
        conversation_id=conversation_id,
        role="user",
        content=payload.content,
    )
    db.add(user_message)
    await db.flush()
    run = AgentRun(
        conversation_id=conversation_id,
        user_message_id=user_message.id,
        model_provider=provider,
        model_name=model,
        prompt_version="medical-v3-focused",
        skill_version="global-citations-semantic-v1",
        status="running",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    collection_id = payload.collection_id or settings.default_knowledge_base_id
    try:
        if payload.execution_mode == "direct":
            model_trace: list[dict] = []
            answer = await generate_with_retry(
                harness,
                payload.content,
                conversation_id=str(conversation_id),
                model=model,
                provider=provider,
                phase="direct_answer",
                trace=model_trace,
            )
            result = (
                AgentResult(answer=answer, trace=model_trace)
                if answer
                else generation_failure(trace=model_trace)
            )
        else:
            agent = MedicalAgent(harness, settings.max_tool_rounds)
            async with MCPToolbox(settings.mcp_servers_json) as toolbox:
                result = await agent.run(
                    question=payload.content,
                    conversation_id=str(conversation_id),
                    history=history,
                    toolbox=toolbox,
                    model=model,
                    provider=provider,
                    tool_context={
                        "collection_id": collection_id,
                        "top_k": payload.retrieval_top_k,
                    },
                )
    except Exception as exc:
        logger.exception("Agent execution failed for conversation %s", conversation_id)
        result = generation_failure(
            trace=[{"stage": "execution", "status": "failed", "error": _exception_detail(exc)}]
        )

    if not result.answer.strip():
        result = generation_failure(calls=result.tool_calls, trace=result.trace)

    assistant_message = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=result.answer,
    )
    db.add(assistant_message)
    await db.flush()
    run.assistant_message_id = assistant_message.id
    persisted_calls: list[ToolCall] = []
    for item in result.tool_calls:
        persisted = ToolCall(
            agent_run_id=run.id,
            mcp_server=item["server"],
            tool_name=item["tool"],
            arguments=item["arguments"],
            result={"content": item["result"]},
            result_hash=item["result_hash"],
            latency_ms=item["latency_ms"],
            status=item["status"],
        )
        db.add(persisted)
        persisted_calls.append(persisted)
    await db.flush()
    citation_call = next(
        (
            item
            for item in persisted_calls
            if item.tool_name in {"search_medical_knowledge", "hybrid_search"}
        ),
        None,
    )
    if citation_call:
        for order, citation in enumerate(result.citations, start=1):
            db.add(
                AnswerCitation(
                    message_id=assistant_message.id,
                    tool_call_id=citation_call.id,
                    document_id=citation.get("document_id"),
                    document_version_id=None,
                    chunk_id=citation.get("chunk_id"),
                    evidence_span_id=None,
                    index_version=citation.get("index_version"),
                    citation_order=order,
                    source_server=citation_call.mcp_server,
                    external_source_id=None,
                )
            )
    run.status = result.status
    run.degraded_without_evidence = result.degraded_without_evidence
    run.trace = result.trace
    run.verification = result.verification
    run.finished_at = datetime.now(UTC)
    record_audit(
        db,
        user_id=payload.user_id,
        action=f"agent.chat.{result.status}",
        resource_type="agent_run",
        resource_id=str(run.id),
        detail={
            "collection_id": collection_id or None,
            "execution_mode": payload.execution_mode,
            "citation_count": len(result.citations),
            "verification_passed": bool(result.verification.get("passed")),
            "degraded": result.degraded_without_evidence,
        },
    )
    await db.commit()
    await db.refresh(assistant_message)

    return ChatResponse(
        run_id=str(run.id),
        message_id=str(assistant_message.id),
        content=result.answer,
        status=result.status,
        citations=result.citations,
        degraded_without_evidence=result.degraded_without_evidence,
        model=model,
        provider=provider,
        tool_calls=result.tool_calls,
        verification=result.verification,
        trace=result.trace,
        links={
            "agent": f"/agent/?run_id={run.id}",
            "evaluation": f"/evaluation/?run_id={run.id}",
            **(
                {"knowledge": f"/knowledge/?collection_id={collection_id}"} if collection_id else {}
            ),
        },
    )


async def _resolve_model_config(
    model_config_id: str | None, db: AsyncSession
) -> ModelConfig | None:
    if model_config_id:
        try:
            parsed_id = uuid.UUID(model_config_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid model_config_id") from exc
        item = await db.get(ModelConfig, parsed_id)
        if item is None or not item.is_active:
            raise HTTPException(status_code=404, detail="Model configuration not found")
        return item
    return await db.scalar(
        select(ModelConfig)
        .where(ModelConfig.is_active.is_(True), ModelConfig.is_default.is_(True))
        .limit(1)
    )
