"""Generation failures must persist a readable assistant reply, not an empty success."""

import uuid
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import app.api.chat as chat_api
from app.models import AgentRun, AuditEvent, Conversation, Message
from app.schemas.evidence import ChatRequest


class MemorySession:
    def __init__(self) -> None:
        self.conversation = Conversation(id=uuid.uuid4(), title="问答", user_id="user")
        self.added: list = []

    async def get(self, _model, _id):
        return self.conversation

    async def scalars(self, _query):
        return SimpleNamespace(all=list)

    async def scalar(self, _query):
        return None

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        for item in self.added:
            if item.id is None:
                item.id = uuid.uuid4()

    async def commit(self):
        await self.flush()

    async def refresh(self, _item):
        pass


class EmptyHarness:
    calls = 0

    def __init__(self, *_args, **_kwargs):
        pass

    async def generate(self, *_args, **_kwargs):
        type(self).calls += 1
        return " \n "


class NoTools:
    def __init__(self, *_args):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def list_tools(self):
        return []


@pytest.mark.parametrize("mode", ["agent", "direct", "tool_setup_failure"])
async def test_generation_failure_persists_failed_run_and_nonempty_reply(monkeypatch, mode):
    session = MemorySession()
    EmptyHarness.calls = 0

    async def consume_quota(*_args):
        pass

    monkeypatch.setattr(chat_api, "consume_quota", consume_quota)
    monkeypatch.setattr(chat_api, "enforce_user", lambda *_args: None)
    monkeypatch.setattr(chat_api, "HarnessService", EmptyHarness)

    class BrokenTools(NoTools):
        async def __aenter__(self):
            raise RuntimeError("MCP unavailable")

    monkeypatch.setattr(
        chat_api, "MCPToolbox", BrokenTools if mode == "tool_setup_failure" else NoTools
    )

    response = await chat_api.chat(
        ChatRequest(
            conversation_id=str(session.conversation.id),
            user_id="user",
            content="question",
            execution_mode="agent" if mode == "tool_setup_failure" else mode,
        ),
        Request({"type": "http", "headers": []}),
        session,
    )

    run = next(item for item in session.added if isinstance(item, AgentRun))
    assistant = next(
        item for item in session.added if isinstance(item, Message) and item.role == "assistant"
    )
    audit = next(item for item in session.added if isinstance(item, AuditEvent))
    assert response.content.strip() == assistant.content
    assert response.status == run.status == "failed"
    assert run.assistant_message_id == assistant.id
    assert audit.action == "agent.chat.failed"
    assert EmptyHarness.calls == (0 if mode == "tool_setup_failure" else 2)
    assert len(run.trace) == (1 if mode == "tool_setup_failure" else 2)
