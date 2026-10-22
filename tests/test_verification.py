from app.services.verification import compact_citations, verify_answer


def test_compacts_used_sources_and_verifies_supported_numbers() -> None:
    citations = [
        {"text": "幕上出血量大于 30ml 时应考虑手术。"},
        {"text": "无关来源"},
    ]
    answer, used = compact_citations("幕上出血量大于 30ml 时应考虑手术 [1]。", citations)

    result = verify_answer(answer, used)

    assert len(used) == 1
    assert result["passed"] is True


def test_rejects_uncited_claim_and_numeric_mismatch() -> None:
    result = verify_answer(
        "幕上出血量大于 50ml 时必须手术 [1]。这是另一条没有引用的长事实陈述。",
        [{"text": "幕上出血量大于 30ml 时应考虑手术。"}],
    )

    assert result["passed"] is False
    assert "numeric_mismatch" in result["issues"]
    assert "uncited_claim" in result["issues"]
