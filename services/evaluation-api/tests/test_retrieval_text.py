import pytest
from fastapi.testclient import TestClient

from evaluation_api.main import app
from evaluation_api.services.evaluation import evaluate_retrieval
from evaluation_api.services.platform_client import extract_retrieved_chunks


def score(references, texts, top_k=5):
    return evaluate_retrieval([{"text": text} for text in texts], [], top_k, references)


@pytest.mark.parametrize(
    ("reference", "retrieved", "expected"),
    [
        ("ＣＴ　１４０ mmHg，检查。", "ct140MMHG检查", 1),
        ("甲乙丙丁", "甲乙", 1 / 3),
        ("甲乙甲乙", "甲乙", 1 / 3),
        ("甲乙丙丁", "无关文本", 0),
        ("1.5 mg", "15 mg", 0.5),
        ("50%", "50", 0.5),
    ],
)
def test_normalization_and_counted_text_overlap(reference, retrieved, expected):
    result = score([reference], [retrieved])
    assert result["method"] == "reference_text_v1"
    assert result["score"] == pytest.approx(expected)


def test_complete_reference_inside_longer_chunk_gets_full_coverage():
    result = score([{"quote": "应尽快完成头颅CT检查"}], ["初步评估后，应尽快完成头颅CT检查，并评估后续处置。"])
    assert result["score"] == 1
    assert 0 < result["metrics"]["text_precision"] < 1
    assert 0 < result["metrics"]["text_f1"] < 1


def test_multi_reference_average_penalizes_missing_reference():
    result = score(["甲乙丙丁", {"context": "戊己庚辛"}], ["甲乙丙丁"])
    assert result["score"] == 0.5
    assert result["metrics"]["reference_count"] == 2
    assert result["matches"][0]["retrieved_rank"] == 1
    assert result["matches"][1]["retrieved_rank"] is None


def test_top_k_excludes_late_retrieval_and_no_results_scores_zero():
    result = score(["甲乙丙丁"], ["无关文本"] * 5 + ["甲乙丙丁"])
    assert result["score"] == 0
    assert result["metrics"]["retrieved_count"] == 5
    empty = score(["甲乙丙丁"], [])
    assert empty["score"] == 0
    assert empty["issues"] == ["no_retrieved_text"]


def test_precision_breaks_equal_coverage_ties():
    result = score(["甲乙丙丁"], ["无关内容甲乙丙丁额外内容", "甲乙丙丁"])
    assert result["matches"][0]["retrieved_rank"] == 2
    assert result["matches"][0]["precision"] == 1


def test_without_reference_text_document_labels_remain_supported():
    result = evaluate_retrieval([{"document_id": "doc-1"}], ["doc-1"], 5)
    assert result["method"] == "document_id"
    assert result["score"] == 1
    assert score([], ["甲乙丙丁"])["score"] is None


def test_raw_retrieval_results_are_deduplicated_and_errors_ignored():
    chunks, source = extract_retrieved_chunks({
        "citations": [{"text": "最终引用只有一条"}],
        "tool_calls": [
            {"tool": "search_medical_knowledge", "is_error": False, "result": {"items": [
                {"chunk_id": "c1", "content": "第一段原文"},
                {"chunk_id": "c2", "text": "未被最终引用的第二段"},
            ]}},
            {"tool": "hybrid_search", "is_error": False, "result": {"items": [
                {"chunk_id": "c1", "content": "第一段原文"},
            ]}},
            {"tool": "search_medical_knowledge", "is_error": True, "result": {"items": [
                {"chunk_id": "bad", "content": "不应纳入"},
            ]}},
        ],
    })
    assert source == "tool_calls"
    assert [chunk["chunk_id"] for chunk in chunks] == ["c1", "c2"]
    assert chunks[1]["text"] == "未被最终引用的第二段"


def test_failed_search_does_not_fall_back_to_citations():
    chunks, source = extract_retrieved_chunks({
        "citations": [{"text": "不能替代失败的检索"}],
        "tool_calls": [{"tool": "search_medical_knowledge", "status": "failed"}],
    })
    assert source == "tool_calls"
    assert chunks == []
    fallback, source = extract_retrieved_chunks({"citations": [{"text": "兼容旧接口"}]})
    assert source == "citations"
    assert fallback == [{"text": "兼容旧接口"}]


def test_score_api_preserves_match_details_and_includes_retrieval_in_overall():
    with TestClient(app) as client:
        response = client.post("/api/v1/evaluations/score", json={
            "question": "测试问题", "answer": "简短回答",
            "citations": [], "reference_chunks": ["甲乙丙丁"],
            "retrieved_chunks": [{"document_id": "retrieved-doc", "text": "甲乙丙丁"}],
        })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["answer"]["score"] is None
    assert result["retrieval"]["score"] == 1
    assert result["retrieval"]["matches"][0]["retrieved_text"] == "甲乙丙丁"
    assert result["overall_score"] == pytest.approx((1 + result["citation"]["score"]) / 2)
