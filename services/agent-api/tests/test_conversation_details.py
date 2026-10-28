import uuid

from agent_api.api.conversations import _citation_payload, _tool_call_payload
from agent_api.models import AnswerCitation, ToolCall


def test_persisted_tool_call_and_citation_restore_full_run_detail() -> None:
    call_id = uuid.uuid4()
    run_id = uuid.uuid4()
    chunk = {
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "index_version": "index-1",
        "document_title": "脑出血诊疗指南",
        "heading_path": ["急性期治疗", "血压管理"],
        "page_start": 12,
        "page_end": 13,
        "content": "建议平稳降低收缩压。",
        "score": 0.93,
        "rank": 1,
    }
    call = ToolCall(
        id=call_id,
        agent_run_id=run_id,
        mcp_server="medical-knowledge",
        tool_name="search_medical_knowledge",
        arguments={"query": "脑出血血压管理"},
        result={"content": {"items": [chunk]}},
        result_hash="abc123",
        latency_ms=42,
        status="completed",
    )
    citation = AnswerCitation(
        id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        tool_call_id=call_id,
        document_id="doc-1",
        document_version_id=None,
        chunk_id="chunk-1",
        evidence_span_id=None,
        index_version="index-1",
        citation_order=1,
        source_server="medical-knowledge",
        external_source_id=None,
    )

    restored_call = _tool_call_payload(call)
    restored_citation = _citation_payload(citation, [call])

    assert restored_call["result"] == {"items": [chunk]}
    assert restored_call["is_error"] is False
    assert restored_citation["title"] == "脑出血诊疗指南"
    assert restored_citation["heading"] == "急性期治疗 › 血压管理"
    assert restored_citation["text"] == "建议平稳降低收缩压。"
    assert restored_citation["page_start"] == 12
