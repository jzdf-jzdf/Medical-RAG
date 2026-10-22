import math

from app.services.embeddings import _article_pairs, builtin_embedding


def test_builtin_embedding_is_normalized_and_deterministic() -> None:
    first = builtin_embedding("脑出血 blood pressure", 32)
    second = builtin_embedding("脑出血 blood pressure", 32)

    assert first == second
    assert math.isclose(sum(value * value for value in first), 1.0)


def test_medcpt_article_input_uses_first_line_as_title() -> None:
    assert _article_pairs(["# 血压管理\n推荐早期降压治疗"]) == [
        ["血压管理", "推荐早期降压治疗"]
    ]
