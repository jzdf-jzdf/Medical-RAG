from app.services.evaluation import evaluate_layers


def test_three_layer_metrics_and_gate() -> None:
    result = evaluate_layers(
        answer="幕上出血量大于 30ml 时应考虑手术 [1]。",
        citations=[{"document_id": "doc-1", "text": "幕上出血量大于 30ml 时应考虑手术。"}],
        expected_document_ids=["doc-1"],
        reference_answer="幕上出血量大于 30ml 时应考虑手术",
        top_k=5,
    )

    assert result["retrieval"]["metrics"]["hit_at_k"] == 1
    assert result["citation"]["score"] == 1
    assert result["answer"]["score"] > 0.8
    assert result["gate_passed"] is True
