from app.services.evidence_extraction import evidence_sentences


def test_evidence_sentences_skip_headings_and_keep_recommendations() -> None:
    content = "#### 2.1 推荐意见\n应监测血压，并避免血压过高或过低。\n背景描述。"

    candidates = evidence_sentences(content)

    assert [quote for _, _, quote in candidates] == ["应监测血压，并避免血压过高或过低。"]
