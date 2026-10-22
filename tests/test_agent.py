from app.services.agent import MedicalAgent, _parse_json_object
from app.services.mcp_client import ToolDefinition, ToolOutput


class FakeModel:
    def __init__(self, replies: list[str]) -> None:
        self.replies = iter(replies)

    async def generate(self, prompt: str, **kwargs) -> str:
        return next(self.replies)


class FakeToolbox:
    def __init__(self, with_tools: bool = True) -> None:
        self.with_tools = with_tools
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        if not self.with_tools:
            return []
        return [
            ToolDefinition(
                name="knowledge__search",
                server="knowledge",
                original_name="search_medical_knowledge",
                description="Search evidence",
                input_schema={"type": "object"},
            )
        ]

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return ToolOutput(
            content={
                "items": [
                    {
                        "document_id": "doc-1",
                        "chunk_id": "chunk-1",
                        "index_version": "v1",
                        "document_title": "Guideline",
                        "content": "evidence based answer",
                    }
                ]
            },
            is_error=False,
        )


async def test_agent_calls_tool_then_returns_answer() -> None:
    model = FakeModel(
        [
            '{"action":"call_tool","tool":"knowledge__search","arguments":{"q":"ICH"}}',
            '{"action":"final","answer":"evidence based answer [1]."}',
        ]
    )
    toolbox = FakeToolbox()
    result = await MedicalAgent(model, max_tool_rounds=2).run(
        question="question",
        conversation_id="conversation",
        history=[],
        toolbox=toolbox,
        model="deepseek-chat",
        provider="deepseek",
    )
    assert result.answer == "evidence based answer [1]."
    assert result.degraded_without_evidence is False
    assert toolbox.calls == [("knowledge__search", {"q": "ICH"})]
    assert result.tool_calls[0]["status"] == "completed"
    assert result.citations[0]["chunk_id"] == "chunk-1"


async def test_agent_can_skip_retrieval_for_non_medical_question() -> None:
    model = FakeModel(
        ['{"action":"final","requires_evidence":false,"answer":"你好，有什么可以帮你？"}']
    )
    result = await MedicalAgent(model).run(
        question="你好",
        conversation_id="conversation",
        history=[],
        toolbox=FakeToolbox(),
        model="deepseek-chat",
        provider="deepseek",
    )
    assert result.answer == "你好，有什么可以帮你？"
    assert result.tool_calls == []
    assert result.degraded_without_evidence is False
    assert result.verification["mode"] == "not_required"


async def test_agent_scopes_retrieval_to_request_collection() -> None:
    model = FakeModel(
        [
            (
                '{"action":"call_tool","tool":"knowledge__search","arguments":'
                '{"collection_id":"wrong","query":"ICH"}}'
            ),
            '{"action":"final","answer":"evidence based answer [1]."}',
        ]
    )
    toolbox = FakeToolbox()
    await MedicalAgent(model).run(
        question="question",
        conversation_id="conversation",
        history=[],
        toolbox=toolbox,
        model="deepseek-chat",
        provider="deepseek",
        tool_context={"collection_id": "selected", "top_k": 6},
    )
    assert toolbox.calls == [
        (
            "knowledge__search",
            {"collection_id": "selected", "query": "ICH", "top_k": 6},
        )
    ]


async def test_agent_marks_no_tool_answer_as_unverified() -> None:
    result = await MedicalAgent(FakeModel(["general answer"])).run(
        question="question",
        conversation_id="conversation",
        history=[],
        toolbox=FakeToolbox(with_tools=False),
        model="deepseek-chat",
        provider="deepseek",
    )
    assert result.degraded_without_evidence is True
    assert "未经文献验证" in result.answer


def test_parse_json_from_markdown_fence() -> None:
    assert _parse_json_object('```json\n{"action":"final","answer":"ok"}\n```') == {
        "action": "final",
        "answer": "ok",
    }
