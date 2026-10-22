from fastapi.testclient import TestClient

from app.main import app
from app.services.platform_client import get_platform_client


class FakePlatformClient:
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

    async def ask_agent(self, *, content: str, title: str, model_config_id=None):
        if title.startswith("出题"):
            assert "分块 0" in content
            return {
                "run_id": "run-generation",
                "content": (
                    '{"question":"急性脑出血首选何种影像检查？",'
                    '"standard_answer":"应尽快完成头颅 CT 检查。",'
                    '"difficulty":"基础","topic":"影像诊断",'
                    '"rationale":"考查首选检查",'
                    '"evidence":[{"chunk_index":0,"heading":"诊断 > 影像检查",'
                    '"quote":"对于急性脑出血患者，应尽快完成头颅 CT 检查。"}]}'
                ),
                "model": "test-model",
                "provider": "test-provider",
            }
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
                },
            )
            agent_test = client.post(
                "/api/v1/agent-tests",
                json={
                    "question": "急性脑出血首选何种影像检查？",
                    "expected_answer": "应尽快完成头颅 CT 检查。",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert knowledge_bases.status_code == 200
    assert knowledge_bases.json()[0]["documents"][0]["title"] == "ICH 指南"
    assert chunks.status_code == 200
    assert chunks.json()["chunks"][0]["heading_path"] == ["诊断", "影像检查"]
    assert question.status_code == 200
    assert question.json()["evidence"][0]["chunk_index"] == 0
    assert question.json()["source_chunks"][0]["index"] == 0
    assert agent_test.status_code == 200
    assert agent_test.json()["run_id"] == "run-test"
    assert agent_test.json()["degraded_without_evidence"] is False


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
