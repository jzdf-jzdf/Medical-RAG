from fastapi.testclient import TestClient

from app.main import app
from app.services.platform_client import PlatformClient, get_platform_client


class FakePlatformClient:
    def __init__(self):
        self.generation_calls = 0

    async def list_knowledge_bases(self):
        return [
            {
                "id": "kb-1",
                "name": "脑出血指南库",
                "description": "测试知识库",
                "status": "ready",
                "documents": [{"id": "doc-1", "title": "ICH 指南"}],
            }
        ]

    async def get_document_chunks(self, collection_id: str, document_id: str):
        assert collection_id == "kb-1"
        assert document_id == "doc-1"
        return {
            "document_id": document_id,
            "document_title": "ICH 指南",
            "method": {"name": "指南分块", "strategy": "heading"},
            "chunks": [
                {
                    "index": 0,
                    "content": "对于急性脑出血患者，应尽快完成头颅 CT 检查。",
                    "embedding_text": "急性脑出血 头颅 CT",
                    "char_start": 0,
                    "char_end": 25,
                    "char_count": 25,
                    "heading_path": ["诊断", "影像检查"],
                }
            ],
            "stats": {
                "total": 1,
                "average_chars": 25,
                "minimum_chars": 25,
                "maximum_chars": 25,
                "too_short": 0,
                "too_long": 0,
            },
        }

    async def ask_agent(
        self,
        *,
        content: str,
        title: str,
        model_config_id=None,
        execution_mode="agent",
    ):
        if title.startswith("出题"):
            self.generation_calls += 1
            assert "分块 0" in content
            assert "生成一道" in content
            assert f"第 {self.generation_calls}/2 道" in content
            assert "患者或家属口吻" in content
            assert execution_mode == "direct"
            question = (
                "我突然出现脑出血相关症状，应尽快做什么检查？"
                if self.generation_calls == 1
                else "我担心是急性脑出血，医院会优先安排哪项影像检查？"
            )
            return {
                "run_id": f"run-generation-{self.generation_calls}",
                "content": (
                    '{'
                    f'"question":"{question}",'
                    '"standard_answer":"应尽快完成头颅 CT 检查。",'
                    '"difficulty":"基础","topic":"影像诊断",'
                    '"rationale":"考查首选检查",'
                    '"evidence":[{"chunk_index":0,"heading":"诊断 > 影像检查",'
                    '"quote":"对于急性脑出血患者，应尽快完成头颅 CT 检查。"}]}'
                ),
                "model": "test-model",
                "provider": "test-provider",
            }
        assert execution_mode == "agent"
        return {
            "run_id": "run-test",
            "content": "应尽快完成头颅 CT 检查。",
            "model": "test-model",
            "provider": "test-provider",
            "degraded_without_evidence": False,
            "citations": [{"chunk_id": "chunk-1"}],
            "tool_calls": [{"tool": "search"}],
        }


def fake_client():
    return FakePlatformClient()


def test_workbench_happy_path() -> None:
    app.dependency_overrides[get_platform_client] = fake_client
    try:
        with TestClient(app) as client:
            knowledge_bases = client.get("/api/v1/knowledge-bases")
            chunks = client.get(
                "/api/v1/knowledge-bases/kb-1/documents/doc-1/chunks"
            )
            question = client.post(
                "/api/v1/questions/generate",
                json={
                    "collection_id": "kb-1",
                    "document_id": "doc-1",
                    "chunk_indexes": [0],
                    "question_count": 2,
                    "question_style": "patient",
                },
            )
            removed_agent_test = client.post(
                "/api/v1/agent-tests",
                json={"question": "急性脑出血首选何种影像检查？"},
            )
    finally:
        app.dependency_overrides.clear()

    assert knowledge_bases.status_code == 200
    assert knowledge_bases.json()[0]["documents"][0]["title"] == "ICH 指南"
    assert chunks.status_code == 200
    assert chunks.json()["chunks"][0]["heading_path"] == ["诊断", "影像检查"]
    assert question.status_code == 200
    assert question.json()["requested_count"] == 2
    assert question.json()["question_type"] == "qa"
    assert question.json()["question_style"] == "patient"
    assert len(question.json()["questions"]) == 2
    assert question.json()["questions"][0]["evidence"][0]["chunk_index"] == 0
    assert question.json()["questions"][0]["source_chunks"][0]["index"] == 0
    assert removed_agent_test.status_code == 404


def test_generate_rejects_unknown_chunk() -> None:
    app.dependency_overrides[get_platform_client] = fake_client
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/questions/generate",
                json={
                    "collection_id": "kb-1",
                    "document_id": "doc-1",
                    "chunk_indexes": [99],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "99" in response.json()["detail"]


async def test_direct_generation_retries_one_empty_agent_response() -> None:
    client = object.__new__(PlatformClient)
    client.agent_url = "http://agent"
    client.user_id = "evaluation-workbench"
    chat_attempts = 0

    async def fake_request(method: str, url: str, **kwargs):
        nonlocal chat_attempts
        if url.endswith("/conversations"):
            return {"id": "conversation-1"}
        chat_attempts += 1
        return {"content": "" if chat_attempts == 1 else '{"questions": []}'}

    client._request = fake_request
    result = await client.ask_agent(
        content="prompt",
        title="出题",
        execution_mode="direct",
    )

    assert chat_attempts == 2
    assert result["content"] == '{"questions": []}'
