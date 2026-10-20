"""Golden Bench School rows come from reference SQL, not from a model."""

from __future__ import annotations

from sqlalchemy.orm import Session

from education_platform.core.config import Settings
from evals.pricing import ModelPrice, parse_catalog_prices
from evals.text_to_sql_pipeline.compare import DEFAULT_GOLDEN, load_questions
from evals.text_to_sql_pipeline.cost import compose_call_count, estimate
from evals.text_to_sql_pipeline.references import references
from evals.text_to_sql_pipeline.rows import canonicalize
from evals.text_to_sql_pipeline.seed import rows_for_session


def test_golden_file_covers_refusals_and_hidden_classes() -> None:
    questions = load_questions(DEFAULT_GOLDEN)
    by_id = {item["id"]: item for item in questions}
    assert len(questions) >= 40
    assert "sample-my-students" in by_id
    refused = [item for item in questions if item.get("expect_refused")]
    hidden = [item for item in questions if item.get("forbidden_names")]
    assert len(refused) >= 8
    assert len(hidden) >= 4
    for item in questions:
        assert "expect_rows" in item
        assert item["route"] in {"template", "free_form", "refuse", "refuse_regex"}
    for item in refused:
        assert item["expect_rows"] == []
    assert by_id["refuse-injection"]["route"] == "refuse_regex"
    assert "Yusuf Ali" in by_id["roster-6b-hidden"]["forbidden_names"]
    ids = [item["id"] for item in questions]
    assert ids == [spec.id for spec in references()]


def test_reference_sql_matches_the_golden_file_and_reloads(db_session: Session) -> None:
    first = rows_for_session(db_session)
    db_session.commit()
    second = rows_for_session(db_session)
    assert second == first
    golden = load_questions(DEFAULT_GOLDEN)
    assert [item["id"] for item in second] == [item["id"] for item in golden]
    for spec, saved in zip(second, golden, strict=True):
        assert canonicalize(spec["expect_rows"]) == canonicalize(saved["expect_rows"])
        assert spec["question"] == saved["question"]


def test_catalog_prices_are_per_token_and_unknown_models_fall_back() -> None:
    prices = parse_catalog_prices(
        [
            {
                "id": "openai/gpt-4o-mini",
                "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
            },
            {"id": "priced-per-million", "pricing": {"prompt": "0.15", "completion": "0.60"}},
        ],
        ["openai/gpt-4o-mini", "priced-per-million", "typesafe/jev-1.14"],
    )
    assert prices["openai/gpt-4o-mini"] == ModelPrice(0.00000015, 0.0000006, "openrouter")
    assert prices["priced-per-million"].prompt_per_token == 0.15 / 1_000_000
    assert prices["typesafe/jev-1.14"].source.startswith("fallback jev")
    assert prices["typesafe/jev-1.14"].completion_per_token == 0


def test_planned_calls_follow_each_route() -> None:
    settings = Settings(_env_file=None, openrouter_api_key=None)  # type: ignore[call-arg]
    price = ModelPrice(0.00000015, 0.0000006, "test")
    jev = ModelPrice(0.000000042, 0.0, "test")
    report = estimate(
        load_questions(DEFAULT_GOLDEN),
        settings,
        {"openai/gpt-4o-mini": price, "typesafe/jev-1.14": jev},
        "test",
    )
    # 41 questions reach Jev. One regex refusal does not. Legacy sends two
    # decisions; lean sends one batch. Free-form is the only legacy generate.
    assert report.legacy.expected["guard"].calls == 82
    assert report.legacy.expected["generate"].calls == 19
    assert report.legacy.pipeline_worst["generate"].calls == 136
    assert report.lean.expected["guard"].calls == 41
    assert report.lean.expected["generate"].calls == 41
    assert report.lean.pipeline_worst["generate"].calls == 76
    assert report.legacy.expected["compose"].calls == 0
    assert report.lean.expected["compose"].calls == 0
    assert report.tokenizer == "tiktoken cl100k_base"


def test_small_tables_do_not_plan_a_compose_call() -> None:
    assert compose_call_count([]) == 0
    assert compose_call_count([{"student_count": 3}]) == 0
    assert compose_call_count([{"full_name": "Omar Haddad", "mastery_percent": "45"}]) == 0
    assert compose_call_count([{"topic": "Fractions", "average_score_percent": "67.5"}]) == 0
