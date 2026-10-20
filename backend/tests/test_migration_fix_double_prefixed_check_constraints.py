"""Tests for migration d0e1f2a3b4c5 (fix double-prefixed CHECK constraint names).

Two things this file proves, neither of which a "the migration ran without erroring"
smoke test would catch:

1. Every one of the 19 renamed constraints still enforces its original CHECK, under its
   new name — a real violating INSERT against each, not just a `pg_constraint` name
   lookup. A rename that silently dropped enforcement (e.g. a typo'd table/constraint
   name in the migration that renamed the wrong thing, or renamed nothing at all and
   failed silently) would still pass a name-only check.
2. `downgrade()` is actually exercised, not assumed correct because `upgrade()` works —
   this project has been burned by that gap before (see the alembic/env.py `fileConfig()`
   incident during Task 10). `test_downgrade_reverses_every_rename_and_reupgrade_restores_them`
   runs a real `alembic downgrade -1` against a live database, confirms all 19 constraints
   are back to their exact original (some hash-truncated) names, then re-upgrades and
   confirms they're correct again — both directions actually run, not read from the
   migration file and trusted.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from education_platform.db.url import to_sync_url

# `tests/` is not an importable package here (no __init__.py) -- redefine locally rather
# than import from conftest.py, same as every other test file in this suite that needs it.
BACKEND_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.usefixtures("clean_db")

# (constraint name after the fix, minimal INSERT that violates *only* this constraint).
# Foreign keys use gen_random_uuid() rather than real seeded rows: confirmed directly
# against this database that Postgres reports a CHECK violation before an FK violation
# for a plain INSERT (checked via a garbage-FK insert against quiz_attempts, which failed
# on the CHECK, not the FK) -- so a nonexistent FK value never masks the CHECK failure
# this test is actually looking for.
_VIOLATING_INSERTS: tuple[tuple[str, str], ...] = (
    (
        "ck_common_mastery_quizzes_exactly_one_target",
        "INSERT INTO common_mastery_quizzes (id, title, quiz_scope, subtopic_id, topic_id) "
        "VALUES (gen_random_uuid(), 'x', 'subtopic_mastery', gen_random_uuid(), gen_random_uuid())",
    ),
    (
        "ck_knowledge_chunks_ordinal",
        "INSERT INTO knowledge_chunks "
        "(id, knowledge_document_version_id, ordinal, text, content_hash) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 0, 'x', 'x')",
    ),
    (
        "ck_knowledge_chunks_token_count",
        "INSERT INTO knowledge_chunks "
        "(id, knowledge_document_version_id, ordinal, text, content_hash, token_count) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'x', 'x', -1)",
    ),
    (
        "ck_knowledge_document_versions_version_number",
        "INSERT INTO knowledge_document_versions "
        "(id, document_id, version_number, lifecycle_status) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 0, 'draft')",
    ),
    (
        "ck_question_versions_marks",
        "INSERT INTO question_versions "
        "(id, question_id, version_number, prompt, question_type, marks, lifecycle_status) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'x', 'multiple_choice', -1, 'draft')",
    ),
    (
        "ck_question_versions_version_number",
        "INSERT INTO question_versions "
        "(id, question_id, version_number, prompt, question_type, marks, lifecycle_status) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 0, 'x', 'multiple_choice', 1, 'draft')",
    ),
    (
        "ck_quiz_attempts_attempt_number",
        "INSERT INTO quiz_attempts (id, student_id, quiz_version_id, attempt_number, status) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 0, 'not_started')",
    ),
    (
        "ck_quiz_attempts_pass_threshold",
        "INSERT INTO quiz_attempts "
        "(id, student_id, quiz_version_id, attempt_number, status, pass_threshold_percent) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 1, 'not_started', 150)",
    ),
    (
        "ck_quiz_attempts_score_percent",
        "INSERT INTO quiz_attempts "
        "(id, student_id, quiz_version_id, attempt_number, status, score_percent) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 1, 'not_started', 150)",
    ),
    (
        "ck_quiz_items_sequence",
        "INSERT INTO quiz_items (id, quiz_version_id, question_version_id, sequence) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 0)",
    ),
    (
        "ck_quiz_releases_window_order",
        "INSERT INTO quiz_releases (id, quiz_version_id, status, window_starts_at, window_ends_at) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'scheduled', '2026-01-02', '2026-01-01')",
    ),
    (
        "ck_quiz_versions_duration_seconds",
        "INSERT INTO quiz_versions "
        "(id, quiz_id, version_number, lifecycle_status, result_release_mode, duration_seconds) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'draft', 'immediate', 0)",
    ),
    (
        "ck_quiz_versions_max_attempts",
        "INSERT INTO quiz_versions "
        "(id, quiz_id, version_number, lifecycle_status, result_release_mode, max_attempts) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'draft', 'immediate', 0)",
    ),
    (
        "ck_quiz_versions_version_number",
        "INSERT INTO quiz_versions "
        "(id, quiz_id, version_number, lifecycle_status, result_release_mode) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 0, 'draft', 'immediate')",
    ),
    (
        "ck_source_chunks_ordinal",
        "INSERT INTO source_chunks (id, source_material_version_id, ordinal, text, content_hash) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 0, 'x', 'x')",
    ),
    (
        "ck_source_chunks_token_count",
        "INSERT INTO source_chunks "
        "(id, source_material_version_id, ordinal, text, content_hash, token_count) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'x', 'x', -1)",
    ),
    (
        "ck_source_material_versions_version_number",
        "INSERT INTO source_material_versions "
        "(id, source_material_id, version_number, lifecycle_status, title, content_format) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 0, 'draft', 'x', 'markdown')",
    ),
    (
        "ck_student_material_progress_last_unit",
        "INSERT INTO student_material_progress "
        "(id, student_subject_enrollment_id, source_material_version_id, status, "
        "opened_at, last_opened_at, last_unit_ordinal) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
        "'opened', now(), now(), 0)",
    ),
    (
        "ck_student_material_progress_status_timestamps",
        "INSERT INTO student_material_progress "
        "(id, student_subject_enrollment_id, source_material_version_id, status, "
        "opened_at, last_opened_at, completed_at) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
        "'completed', now(), now(), NULL)",
    ),
)

assert len(_VIOLATING_INSERTS) == 19  # every renamed constraint, none skipped


@pytest.mark.parametrize("constraint_name,insert_sql", _VIOLATING_INSERTS)
def test_renamed_constraint_still_enforces_its_check(
    db_session: Session, constraint_name: str, insert_sql: str
) -> None:
    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(text(insert_sql))
        db_session.flush()
    db_session.rollback()
    assert constraint_name in str(excinfo.value)


def _all_check_constraint_names(sync_url: str) -> dict[str, set[str]]:
    """{table_name: {constraint_name, ...}} for every CHECK constraint in the schema."""
    engine = create_engine(sync_url, pool_pre_ping=True)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT rel.relname, con.conname FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid WHERE con.contype = 'c'"
            )
        ).fetchall()
    engine.dispose()
    result: dict[str, set[str]] = {}
    for table_name, conname in rows:
        result.setdefault(table_name, set()).add(conname)
    return result


def _alembic_config(sync_url: str) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


# The exact (table, original doubled/truncated name) pairs the migration renames FROM —
# copied from d0e1f2a3b4c5's own _RENAMES, not re-derived, so this test proves the
# migration's *actual* downgrade behavior rather than re-asserting its own source as
# ground truth from a second angle that would trivially agree with it.
_ORIGINAL_NAMES: dict[str, set[str]] = {
    "common_mastery_quizzes": {"ck_common_mastery_quizzes_ck_common_mastery_quizzes_exa_29cb"},
    "knowledge_chunks": {
        "ck_knowledge_chunks_ck_knowledge_chunks_ordinal",
        "ck_knowledge_chunks_ck_knowledge_chunks_token_count",
    },
    "knowledge_document_versions": {"ck_knowledge_document_versions_ck_knowledge_document_ve_06e1"},
    "question_versions": {
        "ck_question_versions_ck_question_versions_marks",
        "ck_question_versions_ck_question_versions_version_number",
    },
    "quiz_attempts": {
        "ck_quiz_attempts_ck_quiz_attempts_attempt_number",
        "ck_quiz_attempts_ck_quiz_attempts_pass_threshold",
        "ck_quiz_attempts_ck_quiz_attempts_score_percent",
    },
    "quiz_items": {"ck_quiz_items_ck_quiz_items_sequence"},
    "quiz_releases": {"ck_quiz_releases_ck_quiz_releases_window_order"},
    "quiz_versions": {
        "ck_quiz_versions_ck_quiz_versions_duration_seconds",
        "ck_quiz_versions_ck_quiz_versions_max_attempts",
        "ck_quiz_versions_ck_quiz_versions_version_number",
    },
    "source_chunks": {
        "ck_source_chunks_ck_source_chunks_ordinal",
        "ck_source_chunks_ck_source_chunks_token_count",
    },
    "source_material_versions": {"ck_source_material_versions_ck_source_material_versions_f04b"},
    "student_material_progress": {
        "ck_student_material_progress_ck_student_material_progre_2be0",
        "ck_student_material_progress_ck_student_material_progre_6a51",
    },
}

_FIXED_NAMES: dict[str, set[str]] = {
    "common_mastery_quizzes": {"ck_common_mastery_quizzes_exactly_one_target"},
    "knowledge_chunks": {"ck_knowledge_chunks_ordinal", "ck_knowledge_chunks_token_count"},
    "knowledge_document_versions": {"ck_knowledge_document_versions_version_number"},
    "question_versions": {"ck_question_versions_marks", "ck_question_versions_version_number"},
    "quiz_attempts": {
        "ck_quiz_attempts_attempt_number",
        "ck_quiz_attempts_pass_threshold",
        "ck_quiz_attempts_score_percent",
    },
    "quiz_items": {"ck_quiz_items_sequence"},
    "quiz_releases": {"ck_quiz_releases_window_order"},
    "quiz_versions": {
        "ck_quiz_versions_duration_seconds",
        "ck_quiz_versions_max_attempts",
        "ck_quiz_versions_version_number",
    },
    "source_chunks": {"ck_source_chunks_ordinal", "ck_source_chunks_token_count"},
    "source_material_versions": {"ck_source_material_versions_version_number"},
    "student_material_progress": {
        "ck_student_material_progress_last_unit",
        "ck_student_material_progress_status_timestamps",
    },
}


def _skip_unless_test_database(url: str) -> None:
    name = urlsplit(url).path.rpartition("/")[2]
    if "test" not in name:
        pytest.skip("refusing to downgrade a database whose name does not contain 'test'")


def test_downgrade_reverses_every_rename_and_reupgrade_restores_them(clean_db: str) -> None:
    sync_url = to_sync_url(clean_db)
    _skip_unless_test_database(sync_url)
    cfg = _alembic_config(sync_url)

    # clean_db already migrated to head -- confirm the fixed names are there first, so a
    # failure below can't be misread as "the fix was never applied in the first place".
    before = _all_check_constraint_names(sync_url)
    for table, names in _FIXED_NAMES.items():
        assert names <= before.get(table, set()), f"{table} missing its fixed name(s) pre-downgrade"

    # Actually run the downgrade -- not assumed correct because upgrade() worked. Target
    # d0e1f2a3b4c5's own predecessor explicitly, not a relative "-1": two migrations
    # (e1f2a3b4c5d6, f7a8b9c0d1e2) have landed on top of it since this test was written,
    # so "-1" would now downgrade one of those instead of the rename migration under test.
    command.downgrade(cfg, "c9d0e1f2a3b4")
    try:
        after_downgrade = _all_check_constraint_names(sync_url)
        for table, names in _ORIGINAL_NAMES.items():
            assert names <= after_downgrade.get(table, set()), (
                f"{table} was not restored to its original name(s) by downgrade()"
            )
        for table, names in _FIXED_NAMES.items():
            assert not (names & after_downgrade.get(table, set())), (
                f"{table} still has a fixed name after downgrade() -- rename did not reverse"
            )
    finally:
        # Always re-upgrade, even on assertion failure, so later tests in the session
        # (which assume head) aren't left on a downgraded schema. Explicit "head", to
        # match the explicit downgrade target above -- also re-applies e1f2a3b4c5d6 and
        # f7a8b9c0d1e2, which the downgrade above necessarily undid too.
        command.upgrade(cfg, "head")

    after_reupgrade = _all_check_constraint_names(sync_url)
    for table, names in _FIXED_NAMES.items():
        assert names <= after_reupgrade.get(table, set()), (
            f"{table} did not return to its fixed name(s) after re-upgrading"
        )
    for table, names in _ORIGINAL_NAMES.items():
        assert not (names & after_reupgrade.get(table, set())), (
            f"{table} still has an original name after re-upgrading"
        )
