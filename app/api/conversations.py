import uuid
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import enforce_user
from app.db.session import get_db
from app.models import AgentRun, AnswerCitation, Conversation, Message, ToolCall
from app.schemas.conversations import (
    ConversationCreate,
    ConversationDetail,
    ConversationList,
    ConversationRead,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate, request: Request, db: AsyncSession = Depends(get_db)
) -> Conversation:
    enforce_user(request, payload.user_id, "editor")
    conversation = Conversation(**payload.model_dump())
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.get("", response_model=ConversationList)
async def list_conversations(
    request: Request,
    user_id: str = Header(alias="X-User-ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ConversationList:
    enforce_user(request, user_id)
    predicate = Conversation.user_id == user_id
    total = int(
        (await db.scalar(select(func.count()).select_from(Conversation).where(predicate))) or 0
    )
    items = list(
        (
            await db.scalars(
                select(Conversation)
                .where(predicate)
                .order_by(Conversation.updated_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return ConversationList(items=items, total=total, page=page, page_size=page_size)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    user_id: str = Header(alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
) -> ConversationDetail:
    enforce_user(request, user_id)
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.user_id != user_id:
        raise HTTPException(status_code=403, detail="Conversation access denied")

    messages = list(
        (
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at, Message.id)
            )
        ).all()
    )
    assistant_message_ids = [item.id for item in messages if item.role == "assistant"]
    runs = (
        list(
            (
                await db.scalars(
                    select(AgentRun).where(
                        AgentRun.assistant_message_id.in_(assistant_message_ids)
                    )
                )
            ).all()
        )
        if assistant_message_ids
        else []
    )
    run_ids = [item.id for item in runs]
    calls = (
        list(
            (
                await db.scalars(
                    select(ToolCall)
                    .where(ToolCall.agent_run_id.in_(run_ids))
                    .order_by(ToolCall.created_at, ToolCall.id)
                )
            ).all()
        )
        if run_ids
        else []
    )
    citations = (
        list(
            (
                await db.scalars(
                    select(AnswerCitation)
                    .where(AnswerCitation.message_id.in_(assistant_message_ids))
                    .order_by(AnswerCitation.message_id, AnswerCitation.citation_order)
                )
            ).all()
        )
        if assistant_message_ids
        else []
    )

    run_by_message = {item.assistant_message_id: item for item in runs}
    calls_by_run: dict[uuid.UUID, list[ToolCall]] = defaultdict(list)
    for call in calls:
        calls_by_run[call.agent_run_id].append(call)
    citations_by_message: dict[uuid.UUID, list[AnswerCitation]] = defaultdict(list)
    for citation in citations:
        citations_by_message[citation.message_id].append(citation)

    stored_messages = []
    for message in messages:
        result = None
        run = run_by_message.get(message.id)
        if run is not None:
            run_calls = calls_by_run[run.id]
            result = {
                "run_id": str(run.id),
                "message_id": str(message.id),
                "content": message.content,
                "citations": [
                    _citation_payload(citation, run_calls)
                    for citation in citations_by_message[message.id]
                ],
                "degraded_without_evidence": run.degraded_without_evidence,
                "model": run.model_name,
                "provider": run.model_provider,
                "tool_calls": [_tool_call_payload(call) for call in run_calls],
                "verification": run.verification or {},
                "trace": run.trace or [],
                "links": {
                    "agent": f"/agent/?run_id={run.id}",
                    "evaluation": f"/evaluation/?run_id={run.id}",
                },
            }
        stored_messages.append(
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
                "result": result,
            }
        )

    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        user_id=conversation.user_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=stored_messages,
    )


def _tool_call_payload(call: ToolCall) -> dict[str, Any]:
    return {
        "server": call.mcp_server,
        "tool": call.tool_name,
        "arguments": call.arguments,
        "result": _unwrap_stored_result(call.result),
        "result_hash": call.result_hash,
        "latency_ms": call.latency_ms,
        "status": call.status,
        "is_error": call.status != "completed",
    }


def _unwrap_stored_result(result: Any) -> Any:
    if isinstance(result, dict) and set(result) == {"content"}:
        return result["content"]
    return result


def _citation_payload(citation: AnswerCitation, calls: list[ToolCall]) -> dict[str, Any]:
    source = _find_retrieved_chunk(citation.chunk_id, calls)
    page = source.get("page")
    return {
        "document_id": citation.document_id,
        "chunk_id": citation.chunk_id,
        "index_version": citation.index_version,
        "title": source.get("document_title") or source.get("title"),
        "heading": " › ".join(source.get("heading_path") or []),
        "page_start": source.get("page_start") or page,
        "page_end": source.get("page_end") or page,
        "text": source.get("content") or source.get("text") or "",
        "score": source.get("score"),
        "rank": source.get("rank"),
    }


def _find_retrieved_chunk(
    chunk_id: str | None, calls: list[ToolCall]
) -> dict[str, Any]:
    if not chunk_id:
        return {}
    for call in calls:
        if call.tool_name not in {"search_medical_knowledge", "hybrid_search"}:
            continue
        payload = _unwrap_stored_result(call.result)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            continue
        for item in payload["items"]:
            if isinstance(item, dict) and item.get("chunk_id") == chunk_id:
                return item
    return {}


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    user_id: str = Header(alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    enforce_user(request, user_id, "editor")
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.user_id != user_id:
        raise HTTPException(status_code=403, detail="Conversation access denied")

    message_ids = select(Message.id).where(Message.conversation_id == conversation_id)
    await db.execute(
        delete(AnswerCitation).where(AnswerCitation.message_id.in_(message_ids))
    )
    await db.execute(delete(AgentRun).where(AgentRun.conversation_id == conversation_id))
    await db.delete(conversation)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
