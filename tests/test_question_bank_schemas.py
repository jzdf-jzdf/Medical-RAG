import pytest
from pydantic import ValidationError

from app.schemas.question_bank import QuestionCreate


def test_single_choice_normalizes_answer_and_options() -> None:
    question = QuestionCreate(
        question_type="single_choice",
        prompt="首选检查是什么？",
        standard_answer="正确答案为头颅 CT。",
        options=[
            {"key": "a", "text": "头颅 CT"},
            {"key": "b", "text": "胸部 X 线"},
        ],
        correct_answer="a",
    )

    assert question.correct_answer == "A"
    assert question.options[0].key == "A"


def test_single_choice_rejects_answer_outside_options() -> None:
    with pytest.raises(ValidationError, match="正确答案"):
        QuestionCreate(
            question_type="single_choice",
            prompt="首选检查是什么？",
            standard_answer="正确答案为头颅 CT。",
            options=[
                {"key": "A", "text": "头颅 CT"},
                {"key": "B", "text": "胸部 X 线"},
            ],
            correct_answer="C",
        )
