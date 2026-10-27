from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.test_runs import _choice_score
from app.db import get_db
from app.main import app
from app.models import Dataset, EvaluationResult, EvaluationRun, Question
from app.services.platform_client import get_platform_client


class FakeScalars:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeDatabase:
    def __init__(self, bank, questions):
        self.bank = bank
        self.questions = questions
        self.results = []

    async def get(self, model, _bank_id):
        return self.bank if model is Dataset else None

    async def scalars(self, _statement):
        return FakeScalars(self.questions)

    def add(self, item):
        if isinstance(item, EvaluationRun):
            item.id = uuid4()
            item.started_at = datetime.now(UTC)
        elif isinstance(item, EvaluationResult):
            item.id = uuid4()
            self.results.append(item)

    async def commit(self):
        return None

    async def refresh(self, _item):
        return None

    async def execute(self, _statement):
        return FakeRows(
            [
                (
                    item,
                    question.prompt,
                    question.question_type,
                    question.options,
                    question.correct_answer,
                )
                for item, question in zip(self.results, self.questions)
            ]
        )


class FakeAgent:
    seen_collection_ids: ClassVar[list[str]] = []
    seen_prompts: ClassVar[list[str]] = []

    async def list_knowledge_bases(self):
        return [{"id": "78cff297-3d57-4135-8c34-eba69a6d24a2", "name": "测试知识库"}]

    async def ask_agent(self, *, content, collection_id, **_kwargs):
        type(self).seen_collection_ids.append(collection_id)
        type(self).seen_prompts.append(content)
        answer = "B" if "这是一道单选题" in content else "患者应尽快完成头颅 CT 检查。"
        return {
            "run_id": f"agent-{len(content)}",
            "content": answer,
            "citations": [],
        }


def fake_db_provider(database):
    async def provider():
        yield database

    return provider


def test_run_question_bank_persists_each_agent_result() -> None:
    bank_id = uuid4()
    bank = Dataset(id=bank_id, name="测试题库", version="1.0", description=None)
    questions = [
        Question(
            id=uuid4(),
            dataset_id=bank_id,
            prompt="问答：脑出血首选什么检查？",
            gold_answer="应尽快完成头颅 CT 检查。",
            question_type="qa",
            options=[],
            correct_answer=None,
            metadata_json={},
            gold_evidence=[],
            source="manual",
        ),
        Question(
            id=uuid4(),
            dataset_id=bank_id,
            prompt="单选：脑出血首选检查？",
            gold_answer="B 是正确选项。",
            question_type="single_choice",
            options=[{"key": "A", "text": "血常规"}, {"key": "B", "text": "头颅 CT"}],
            correct_answer="B",
            metadata_json={},
            gold_evidence=[],
            source="manual",
        ),
    ]
    database = FakeDatabase(bank, questions)
    FakeAgent.seen_collection_ids.clear()
    FakeAgent.seen_prompts.clear()
    app.dependency_overrides[get_db] = fake_db_provider(database)
    app.dependency_overrides[get_platform_client] = FakeAgent
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/test-runs",
                json={
                    "bank_id": str(bank_id),
                    "collection_id": "78cff297-3d57-4135-8c34-eba69a6d24a2",
                },
            )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["bank_name"] == "测试题库"
        assert payload["status"] == "completed"
        assert payload["total_questions"] == payload["completed_questions"] == 2
        assert len(payload["results"]) == 2
        assert payload["results"][0]["question_type"] == "qa"
        assert payload["results"][1]["question_type"] == "single_choice"
        assert payload["results"][1]["correct_answer"] == "B"
        assert payload["results"][1]["options"][1]["key"] == "B"
        assert payload["results"][1]["metrics"]["answer"]["metrics"]["choice_accuracy"] == 1.0
        assert FakeAgent.seen_collection_ids == ["78cff297-3d57-4135-8c34-eba69a6d24a2"] * 2
        assert FakeAgent.seen_prompts[0].startswith("这是一道问答题")
        assert "这是一道单选题" in FakeAgent.seen_prompts[1]
        assert "A. 血常规" in FakeAgent.seen_prompts[1]
        assert "B. 头颅 CT" in FakeAgent.seen_prompts[1]
    finally:
        app.dependency_overrides.clear()


def test_choice_score_recognizes_answer_key() -> None:
    question = Question(correct_answer="B", options=[{"key": "B", "text": "头颅 CT"}])
    assert _choice_score("根据指南，答案为 B。", question) == (1.0, "B")
    assert _choice_score("B. 头颅 CT 是首选检查。", question) == (1.0, "B")
