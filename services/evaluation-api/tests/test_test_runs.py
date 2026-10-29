import asyncio
from datetime import UTC, datetime
from typing import ClassVar
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from evaluation_api.api.test_runs import _choice_score
from evaluation_api.db import get_db
from evaluation_api.main import app
from evaluation_api.models import Dataset, EvaluationResult, EvaluationRun, Question
from evaluation_api.services.platform_client import get_platform_client
from evaluation_api.services.run_queue import get_run_queue
from evaluation_api.services.test_run_execution import execute_evaluation_run


class FakeScalars:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items

    def __iter__(self):
        return iter(self.items)


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeDatabase:
    def __init__(self, bank, questions):
        self.bank = bank
        self.questions = questions

    async def get(self, model, _bank_id):
        return self.bank if model is Dataset else None

    async def scalars(self, _statement):
        return FakeScalars(self.questions)

    def add(self, item):
        if isinstance(item, EvaluationRun):
            item.id = uuid4()
            item.started_at = datetime.now(UTC)
            item.attempt_count = 0
            item.cancel_requested = False
            item.error = None
            item.finished_at = None

    async def commit(self):
        return None

    async def refresh(self, _item):
        return None

    async def execute(self, _statement):
        return FakeRows([])


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
            "tool_calls": [{
                "tool": "search_medical_knowledge", "is_error": False,
                "result": {"items": [
                    {"chunk_id": "unrelated", "content": "与题目无关的内容"},
                    {"chunk_id": "matched", "content": "患者应尽快完成头颅 CT 检查。"},
                ]},
            }],
        }


class FakeQueue:
    enqueued: ClassVar[list] = []

    async def enqueue(self, run_id):
        type(self).enqueued.append(run_id)
        return f"task-{run_id}"


def fake_db_provider(database):
    async def provider():
        yield database

    return provider


def questions_for(bank_id):
    return [
        Question(
            id=uuid4(),
            dataset_id=bank_id,
            prompt="问答：脑出血首选什么检查？",
            gold_answer="",
            question_type="qa",
            options=[],
            correct_answer=None,
            metadata_json={},
            gold_evidence=[{"quote": "患者应尽快完成头颅 CT 检查。"}],
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


def test_create_run_returns_queued_and_dispatches_without_calling_agent() -> None:
    bank_id = uuid4()
    bank = Dataset(id=bank_id, name="测试题库", version="1.0", description=None)
    database = FakeDatabase(bank, questions_for(bank_id))
    FakeQueue.enqueued.clear()
    FakeAgent.seen_prompts.clear()
    app.dependency_overrides[get_db] = fake_db_provider(database)
    app.dependency_overrides[get_platform_client] = FakeAgent
    app.dependency_overrides[get_run_queue] = FakeQueue
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/test-runs",
                json={
                    "bank_id": str(bank_id),
                    "collection_id": "78cff297-3d57-4135-8c34-eba69a6d24a2",
                },
            )
        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["status"] == "queued"
        assert payload["total_questions"] == 2
        assert payload["completed_questions"] == 0
        assert payload["results"] == []
        assert [str(item) for item in FakeQueue.enqueued] == [payload["id"]]
        assert FakeAgent.seen_prompts == []
    finally:
        app.dependency_overrides.clear()


class RunnerRows:
    def __init__(self, first_value):
        self.first_value = first_value

    def first(self):
        return self.first_value


class RunnerSession:
    def __init__(self, run, bank, questions):
        self.run = run
        self.bank = bank
        self.questions = questions
        self.added = []
        self.execute = AsyncMock(return_value=RunnerRows((run, bank)))
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self._scalar_calls = 0

    async def scalars(self, _statement):
        self._scalar_calls += 1
        if self._scalar_calls == 1:
            return FakeScalars(self.questions)
        if self._scalar_calls == 2:
            return FakeScalars([self.questions[0].id])
        return FakeScalars(["completed", "completed"])

    def add(self, item):
        self.added.append(item)


class RunnerSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return None


def test_worker_resumes_from_persisted_question_results() -> None:
    bank_id = uuid4()
    run_id = uuid4()
    bank = Dataset(id=bank_id, name="测试题库", version="1.0", description=None)
    questions = questions_for(bank_id)
    run = EvaluationRun(
        id=run_id,
        dataset_id=bank_id,
        status="queued",
        config={"collection_id": "78cff297-3d57-4135-8c34-eba69a6d24a2", "scoring_top_k": 5},
        total_questions=2,
        completed_questions=1,
        attempt_count=0,
        cancel_requested=False,
        worker_task_id=None,
        heartbeat_at=None,
        last_enqueued_at=None,
        error=None,
    )
    session = RunnerSession(run, bank, questions)
    FakeAgent.seen_prompts.clear()
    asyncio.run(
        execute_evaluation_run(
            run_id,
            "worker-task-2",
            session_factory=lambda: RunnerSessionContext(session),
            client=FakeAgent(),
        )
    )
    added = [item for item in session.added if isinstance(item, EvaluationResult)]
    assert len(added) == 1
    assert added[0].question_id == questions[1].id
    assert run.completed_questions == 2
    assert run.status == "completed"
    assert run.attempt_count == 1
    assert len(FakeAgent.seen_prompts) == 1
    assert "这是一道单选题" in FakeAgent.seen_prompts[0]


def test_choice_score_recognizes_answer_key() -> None:
    question = Question(correct_answer="B", options=[{"key": "B", "text": "头颅 CT"}])
    assert _choice_score("根据指南，答案为 B。", question) == (1.0, "B")
    assert _choice_score("B. 头颅 CT 是首选检查。", question) == (1.0, "B")
