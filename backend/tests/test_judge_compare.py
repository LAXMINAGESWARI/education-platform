"""Offline comparison of Jev verdicts with stored blind-solve results."""

from __future__ import annotations

import json
from pathlib import Path

from education_platform.modules.judge.checks import ItemTarget, LessonTarget
from education_platform.modules.judge.compare import (
    BankItem,
    BankLesson,
    compare_bank,
    load_bank_json,
    render_report,
)
from education_platform.modules.judge.types import (
    ChoiceAnswer,
    DecisionRequest,
    DecisionResult,
    NoulAnswer,
    ScoreAnswer,
)


class _AgreeJudge:
    def decide(self, request: DecisionRequest) -> DecisionResult:
        answers: dict[str, NoulAnswer | ChoiceAnswer | ScoreAnswer] = {}
        for check_id, question in request.questions.items():
            if question.to_api()["type"] == "choice":
                answers[check_id] = ChoiceAnswer(
                    choice="easy",
                    probabilities={"easy": 0.8, "medium": 0.2, "hard": 0.0},
                    confidence=0.7,
                )
            elif question.to_api()["type"] == "score":
                answers[check_id] = ScoreAnswer(
                    score=2.5,
                    probabilities={"0": 0.0, "1": 0.1, "2": 0.4, "3": 0.5},
                    confidence=0.8,
                    legend={"0": "none", "1": "partial", "2": "mostly", "3": "full"},
                )
            else:
                answers[check_id] = NoulAnswer(0.95)
        return DecisionResult(
            model="typesafe/jev-1.14",
            answers=answers,
            input_tokens=200,
            output_tokens=40,
            cost_usd=0.0000084,
            latency_ms=25,
            request_id="gen-dec-compare",
            provider="TypeSafe",
        )


def _item(*, status: str, prompt: str = "Which shape has four right angles?") -> BankItem:
    return BankItem(
        key=prompt,
        target=ItemTarget(
            heading="Shapes",
            grade="Grade 6",
            outcomes=("Identify rectangles.",),
            chunks=("A rectangle has four right angles.",),
            prompt=prompt,
            options={"A": "Rectangle", "B": "Circle", "C": "Line", "D": "Point"},
            correct_label="A",
            correct_rationale="A rectangle has four right angles.",
            difficulty="easy",
        ),
        blind_status=status,
        blind_answer="B" if status == "disagree" else "A",
        blind_working="Counted the angles."
        if status == "disagree"
        else "The stem names a rectangle.",
    )


def test_agreement_rate_and_disagreement_rationales(tmp_path: Path) -> None:
    lessons = [
        BankLesson(
            key="area",
            target=LessonTarget(
                heading="Area",
                grade="Grade 6",
                outcomes=("Find the area of a rectangle.",),
                chunks=("Area is length times width.",),
                markdown="The area of a rectangle is length times width.",
            ),
        )
    ]
    items = [_item(status="agree"), _item(status="disagree", prompt="What is a circle?")]
    report = compare_bank(lessons, items, _AgreeJudge())
    assert report.comparable == 2
    assert report.agreements == 1
    assert report.agreement_rate == 0.5
    assert len(report.disagreements) == 1
    row = report.disagreements[0]
    assert row.blind_status == "disagree"
    assert row.blind_working == "Counted the angles."
    assert row.jev_decision == "accept"
    text = render_report(report)
    assert "Agreement rate: 50.0%" in text
    assert "Counted the angles." in text
    assert report.cost_usd > 0

    path = tmp_path / "bank.json"
    path.write_text(
        json.dumps(
            {
                "lessons": [],
                "items": [
                    {
                        "id": "q1",
                        "heading": "Shapes",
                        "grade": "Grade 6",
                        "outcomes": ["Identify rectangles."],
                        "chunks": ["A rectangle has four right angles."],
                        "prompt": "Which shape has four right angles?",
                        "options": {"A": "Rectangle", "B": "Circle", "C": "Line", "D": "Point"},
                        "correct_label": "A",
                        "correct_rationale": "A rectangle has four right angles.",
                        "difficulty": "easy",
                        "blind_solve": {
                            "status": "agree",
                            "answer": "A",
                            "working": "Four right angles.",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded_lessons, loaded_items = load_bank_json(path)
    assert loaded_lessons == []
    assert loaded_items[0].blind_status == "agree"
    assert loaded_items[0].blind_working == "Four right angles."
