from app.api.chat import _exception_detail
from app.services.agent import MedicalAgent, _extract_knowledge_evidence, _parse_json_object
from app.services.mcp_client import MCPToolbox, ToolDefinition, ToolOutput, _mcp_http_timeout


class FakeModel:
    def __init__(self, replies: list[str]) -> None:
        self.replies = iter(replies)
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
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
    semantic = (
        '{"claims":[{"claim_id":1,"supported":true,"evidence":['
        '{"citation_id":1,"quote":"evidence based answer"}],"reason":""}]}'
    )
    model = FakeModel(
        [
            '{"action":"call_tool","tool":"knowledge__search","arguments":{"q":"ICH"}}',
            '{"action":"final","requires_evidence":true}',
            "evidence based answer [1].",
            semantic,
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
    assert result.verification["semantic_passed"] is True
    assert '"citation": 1' in model.prompts[2]


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
    semantic = (
        '{"claims":[{"claim_id":1,"supported":true,"evidence":['
        '{"citation_id":1,"quote":"evidence based answer"}],"reason":""}]}'
    )
    model = FakeModel(
        [
            (
                '{"action":"call_tool","tool":"knowledge__search","arguments":'
                '{"collection_id":"wrong","query":"ICH"}}'
            ),
            '{"action":"final","requires_evidence":true}',
            "evidence based answer [1].",
            semantic,
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


async def test_verification_failure_is_metadata_not_answer_text() -> None:
    model = FakeModel(
        [
            '{"action":"call_tool","tool":"knowledge__search","arguments":{}}',
            '{"action":"final","requires_evidence":true}',
            "the dose is 30 mg [1].",
            '{"claims":[{"claim_id":1,"supported":false,"evidence":[],"reason":"剂量不符"}]}',
        ]
    )
    result = await MedicalAgent(model).run(
        question="dose",
        conversation_id="conversation",
        history=[],
        toolbox=FakeToolbox(),
        model="deepseek-chat",
        provider="deepseek",
    )

    assert result.verification["passed"] is False
    assert "numeric_mismatch" in result.verification["issues"]
    assert "引用核验未通过" not in result.answer


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


def test_retrieved_chunks_receive_one_global_deduplicated_order() -> None:
    calls = [
        {
            "tool": "search_medical_knowledge",
            "is_error": False,
            "result": {
                "items": [
                    {"chunk_id": "chunk-1", "content": "first"},
                    {"chunk_id": "chunk-2", "content": "second"},
                ]
            },
        },
        {
            "tool": "hybrid_search",
            "is_error": False,
            "result": {
                "items": [
                    {"chunk_id": "chunk-1", "content": "duplicate"},
                    {"chunk_id": "chunk-3", "content": "third"},
                ]
            },
        },
    ]

    citations, _ = _extract_knowledge_evidence(calls)

    assert [item["chunk_id"] for item in citations] == ["chunk-1", "chunk-2", "chunk-3"]


def test_exception_detail_unwraps_nested_task_group_error() -> None:
    error = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [ConnectionResetError("peer reset")],
    )

    detail = _exception_detail(error)

    assert "ConnectionResetError" in detail
    assert "peer reset" in detail
    assert "unhandled errors" not in detail


class _FailingExitStack:
    async def __aexit__(self, *_args) -> None:
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError("transport closed")],
        )


async def test_mcp_cleanup_failure_does_not_escape_toolbox_exit() -> None:
    toolbox = MCPToolbox([])
    toolbox._stack = _FailingExitStack()

    await toolbox.__aexit__(None, None, None)


def test_mcp_http_timeout_uses_configured_value() -> None:
    timeout = _mcp_http_timeout(30)

    assert timeout.connect == 30
    assert timeout.read == 30
    assert timeout.write == 30
    assert timeout.pool == 30
