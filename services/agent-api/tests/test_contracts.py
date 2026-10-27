import asyncio

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import MCPServerSettings
from app.main import app
from app.schemas.evidence import ChatRequest, CitationCandidate
from app.services.mcp import MCPOrchestrator


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200


def test_partial_knowledge_identity_is_rejected() -> None:
    try:
        CitationCandidate(source_server="knowledge", document_id="doc-only")
    except ValidationError:
        return
    raise AssertionError("partial stable identity must be rejected")


def test_chat_request_defaults_to_agent_and_accepts_direct_mode() -> None:
    default = ChatRequest(conversation_id="conversation", user_id="user", content="question")
    direct = ChatRequest(
        conversation_id="conversation",
        user_id="user",
        content="question",
        execution_mode="direct",
    )

    assert default.execution_mode == "agent"
    assert direct.execution_mode == "direct"


async def test_mcp_partial_failure_is_isolated() -> None:
    servers = [
        MCPServerSettings(name="a", transport="stdio", command="noop", allowed_tools=["search"]),
        MCPServerSettings(name="b", transport="stdio", command="noop", allowed_tools=["search"]),
    ]

    async def caller(server, tool, arguments):
        if server.name == "b":
            raise RuntimeError("offline")
        await asyncio.sleep(0)
        return {"items": []}

    results = await MCPOrchestrator(servers, caller).call_many(
        [("a", "search", {}), ("b", "search", {})]
    )
    assert [item.status for item in results] == ["completed", "failed"]
