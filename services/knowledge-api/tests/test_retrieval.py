from app.services.retrieval import _lexical_score


def test_lexical_score_matches_chinese_bigrams() -> None:
    relevant = _lexical_score("脑出血患者的血压管理", "2.2 血压与脑出血转归")
    unrelated = _lexical_score("脑出血患者的血压管理", "体温与血糖控制")

    assert relevant > unrelated
    assert relevant > 0
