"""`GET /insights/dashboard` -- the admin dashboard's headline numbers.

The endpoint aggregates in SQL, so these tests check its answers against the register
(`/insights/students`), which the rest of the suite already trusts. If the two ever disagree,
one of them is wrong.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from education_platform.db.url import to_sync_url
from education_platform.modules.synthetic.generator import SchoolSpec, generate_school

TEST_SPEC = SchoolSpec(sections_per_grade=2, students_per_section=3, term_weeks=2)
SCHOOL = TEST_SPEC.institution_name
PASSWORD = "demo1234"

ADMIN = "fatima.almansouri@alnoor.school"
TEACHER = "meera.krishnan@alnoor.school"


@pytest.fixture()
def api(client: TestClient, clean_db: str) -> Iterator[TestClient]:
    """The app, backed by a generated school."""
    engine = create_engine(to_sync_url(clean_db), pool_pre_ping=True)
    with Session(engine) as session:
        generate_school(session, TEST_SPEC)
        session.commit()
    engine.dispose()
    yield client


def _headers(api: TestClient, email: str) -> dict[str, str]:
    response = api.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "institution_name": SCHOOL},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _dashboard(api: TestClient, email: str) -> dict:
    response = api.get("/api/v1/insights/dashboard", headers=_headers(api, email))
    assert response.status_code == 200, response.text
    return response.json()


def _register(api: TestClient, email: str) -> list[dict]:
    body = api.get("/api/v1/insights/students?limit=500", headers=_headers(api, email)).json()
    assert body["rows_returned"] < 500, "test school outgrew the register limit"
    return body["items"]


def test_admin_dashboard_matches_the_register(api: TestClient) -> None:
    dashboard = _dashboard(api, ADMIN)
    rows = _register(api, ADMIN)

    assert dashboard["scope_description"] == "Whole institution"
    assert dashboard["total_students"] == len({row["student_id"] for row in rows}) > 0

    # Attendance is whole-day: one value per student, not one per subject row.
    attendance = {
        row["student_id"]: row["attendance_percent"]
        for row in rows
        if row["attendance_percent"] is not None
    }
    if attendance:
        expected = sum(attendance.values()) / len(attendance)
        assert dashboard["average_attendance"] == pytest.approx(expected, abs=0.06)
    else:
        assert dashboard["average_attendance"] is None


def test_mastery_ignores_subjects_never_quizzed(api: TestClient) -> None:
    """A never-quizzed subject shows as 0% in student_360; it must not count as a real 0."""
    dashboard = _dashboard(api, ADMIN)
    attempted = [row for row in _register(api, ADMIN) if row["quizzes_taken"] > 0]

    if attempted:
        expected = sum(row["mastery_percent"] for row in attempted) / len(attempted)
        assert dashboard["average_mastery"] == pytest.approx(expected, abs=0.06)
    else:
        assert dashboard["average_mastery"] is None


def test_subjects_are_listed_weakest_first(api: TestClient) -> None:
    subjects = _dashboard(api, ADMIN)["subjects"]
    assert subjects, "the generated school has enrolments, so there must be subjects"

    scored = [s["average_mastery"] for s in subjects if s["average_mastery"] is not None]
    assert scored == sorted(scored)

    # Subjects with no attempts yet sort after every scored one.
    seen_unscored = False
    for subject in subjects:
        if subject["average_mastery"] is None:
            seen_unscored = True
            assert subject["students_attempted"] == 0
        else:
            assert not seen_unscored

    for subject in subjects:
        assert 0 <= subject["students_attempted"] <= subject["students"]
        assert 0 <= subject["quizzes_passed"] <= subject["quizzes_taken"]


def test_teacher_sees_only_their_own_classes(api: TestClient) -> None:
    """Same endpoint, narrower answer -- the scope comes from the token, not a parameter."""
    admin = _dashboard(api, ADMIN)
    teacher = _dashboard(api, TEACHER)

    assert 0 < teacher["total_students"] < admin["total_students"]
    assert "assignment" in teacher["scope_description"]
    assert teacher["total_students"] == len({row["student_id"] for row in _register(api, TEACHER)})
