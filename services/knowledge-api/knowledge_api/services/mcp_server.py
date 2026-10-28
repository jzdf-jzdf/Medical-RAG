"""Official MCP v2 server exposing read-only knowledge retrieval tools."""

import uuid

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from sqlalchemy import select

from knowledge_api.db.session import SessionLocal
from knowledge_api.models import Chunk, Collection, Document
from knowledge_api.services.retrieval import search_collection

mcp_server = MCPServer(
    name="medical-knowledge",
    title="Medical Knowledge Retrieval",
    description="Search indexed medical knowledge bases with traceable document citations.",
    version="0.1.0",
)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@mcp_server.tool(annotations=READ_ONLY, structured_output=True)
async def list_knowledge_bases() -> dict[str, object]:
    """List knowledge bases and whether their vector indexes are searchable."""

    async with SessionLocal() as db:
        rows = list((await db.scalars(select(Collection).order_by(Collection.name))).all())
    return {
        "items": [
            {
                "id": str(row.id),
                "name": row.name,
                "description": row.description,
                "status": row.status,
            }
            for row in rows
        ]
    }


@mcp_server.tool(annotations=READ_ONLY, structured_output=True)
async def search_medical_knowledge(
    collection_id: str,
    query: str,
    top_k: int = 8,
) -> dict[str, object]:
    """Search one indexed knowledge base and return ranked, cited passages."""

    try:
        parsed_id = uuid.UUID(collection_id)
    except ValueError as exc:
        raise ValueError("collection_id 必须是有效 UUID") from exc
    async with SessionLocal() as db:
        return await search_collection(db, parsed_id, query, top_k=max(1, min(top_k, 50)))


@mcp_server.tool(annotations=READ_ONLY, structured_output=True)
async def get_knowledge_chunk(chunk_id: str) -> dict[str, object]:
    """Read one exact knowledge chunk with its source document citation."""

    try:
        parsed_id = uuid.UUID(chunk_id)
    except ValueError as exc:
        raise ValueError("chunk_id 必须是有效 UUID") from exc
    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(Chunk, Document)
                .join(Document, Document.id == Chunk.document_id)
                .where(Chunk.id == parsed_id)
            )
        ).one_or_none()
    if row is None:
        raise ValueError("知识块不存在")
    chunk, document = row
    return {
        "chunk_id": str(chunk.id),
        "document_id": str(document.id),
        "document_title": document.title,
        "heading_path": chunk.heading_path or [],
        "content": chunk.content,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "index_version": chunk.index_version,
    }


mcp_http_app = mcp_server.streamable_http_app(
    streamable_http_path="/",
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "127.0.0.1:*",
            "localhost:*",
            "127.0.0.1",
            "localhost",
            "knowledge-api:8001",
        ],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    ),
)
