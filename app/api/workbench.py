"""Chunk browsing, question generation, and live Agent testing routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.workbench import (
    AgentTestRequest,
    AgentTestResult,
    ChunkItem,
    ChunkStats,
    DocumentChunks,
    DocumentSummary,
    GeneratedQuestion,
    GenerateQuestionRequest,
    KnowledgeBaseSummary,
)
from app.services.platform_client import (
    PlatformClient,
    PlatformServiceError,
    build_question_prompt,
    get_platform_client,
    parse_generated_question,
)

router = APIRouter(tags=["testing workbench"])
PlatformClientDependency = Annotated[PlatformClient, Depends(get_platform_client)]


def _upstream_error(exc: PlatformServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseSummary])
async def list_knowledge_bases(
    client: PlatformClientDependency,
) -> list[KnowledgeBaseSummary]:
    try:
        items = await client.list_knowledge_bases()
        return [
            KnowledgeBaseSummary(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or "未命名知识库"),
                description=item.get("description"),
                status=str(item.get("status") or "unknown"),
                documents=[
                    DocumentSummary(id=str(doc.get("id") or ""), title=str(doc.get("title") or ""))
                    for doc in item.get("documents", [])
                    if isinstance(doc, dict) and doc.get("id")
                ],
            )
            for item in items
            if item.get("id")
        ]
    except PlatformServiceError as exc:
        raise _upstream_error(exc) from exc


@router.get(
    "/knowledge-bases/{collection_id}/documents/{document_id}/chunks",
    response_model=DocumentChunks,
)
async def get_document_chunks(
    collection_id: str,
    document_id: str,
    client: PlatformClientDependency,
) -> DocumentChunks:
    try:
        payload = await client.get_document_chunks(collection_id, document_id)
        method = payload.get("method") or {}
        return DocumentChunks(
            document_id=str(payload.get("document_id") or document_id),
            document_title=str(payload.get("document_title") or ""),
            method_name=str(method.get("name") or "分块方法"),
            method_strategy=str(method.get("strategy") or "unknown"),
            chunks=[ChunkItem.model_validate(item) for item in payload.get("chunks", [])],
            stats=ChunkStats.model_validate(payload.get("stats") or {}),
        )
    except PlatformServiceError as exc:
        raise _upstream_error(exc) from exc


@router.post("/questions/generate", response_model=GeneratedQuestion)
async def generate_question(
    payload: GenerateQuestionRequest,
    client: PlatformClientDependency,
) -> GeneratedQuestion:
    try:
        document = await client.get_document_chunks(payload.collection_id, payload.document_id)
        chunks = document.get("chunks") or []
        selected_by_index = {
            int(item.get("index")): item
            for item in chunks
            if isinstance(item, dict) and item.get("index") is not None
        }
        missing = [index for index in payload.chunk_indexes if index not in selected_by_index]
        if missing:
            raise HTTPException(status_code=422, detail=f"分块不存在：{missing}")
        selected = [selected_by_index[index] for index in payload.chunk_indexes]
        document_title = str(document.get("document_title") or "")
        result = await client.ask_agent(
            content=build_question_prompt(
                document_title=document_title,
                chunks=selected,
                instructions=payload.instructions,
            ),
            title=f"出题：{document_title}",
            model_config_id=payload.model_config_id,
        )
        return parse_generated_question(
            str(result.get("content") or ""),
            source_chunks=selected,
            agent_result=result,
        )
    except PlatformServiceError as exc:
        raise _upstream_error(exc) from exc


@router.post("/agent-tests", response_model=AgentTestResult)
async def run_agent_test(
    payload: AgentTestRequest,
    client: PlatformClientDependency,
) -> AgentTestResult:
    try:
        result = await client.ask_agent(
            content=payload.question.strip(),
            title=f"测评：{payload.question.strip()[:80]}",
            model_config_id=payload.model_config_id,
        )
        return AgentTestResult(
            answer=str(result.get("content") or ""),
            expected_answer=payload.expected_answer,
            run_id=str(result.get("run_id") or ""),
            model=str(result.get("model") or "unknown"),
            provider=str(result.get("provider") or "unknown"),
            degraded_without_evidence=bool(result.get("degraded_without_evidence", False)),
            citations=list(result.get("citations") or []),
            tool_calls=list(result.get("tool_calls") or []),
            verification=dict(result.get("verification") or {}),
            trace=list(result.get("trace") or []),
            links=dict(result.get("links") or {}),
        )
    except PlatformServiceError as exc:
        raise _upstream_error(exc) from exc
