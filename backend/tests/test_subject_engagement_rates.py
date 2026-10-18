"""Subject-scoped engagement rates used by the feedback page."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from education_platform.modules.academics.models import Subject
from education_platform.modules.materials.seed import POC_INSTITUTION_NAME, POC_SUBJECT_CODE


def test_subject_engagement_rates_match_the_per_subtopic_endpoint(
    client: TestClient, enrolled_student_headers: dict[str, str]
) -> None:
    directory = client.get("/api/v1/me/learning-directory", headers=enrolled_student_headers)
    assert directory.status_code == 200, directory.text
    subject = directory.json()["subjects"][0]
    subtopic_id = subject["topics"][0]["subtopics"][0]["id"]

    batched = client.get(
        f"/api/v1/subjects/{subject['id']}/engagement-rates",
        headers=enrolled_student_headers,
    )
    assert batched.status_code == 200, batched.text
    single = client.get(
        f"/api/v1/me/lessons/{subtopic_id}/engagement-rate",
        headers=enrolled_student_headers,
    )
    assert single.status_code == 200, single.text

    match = next(row for row in batched.json() if row["subtopic_id"] == subtopic_id)
    assert match == single.json()
    assert match["total_slides"] > 0
    assert len(batched.json()) >= 2


def test_subject_engagement_rates_404_when_not_enrolled(
    client: TestClient, seeded_db: Session
) -> None:
    provision = client.post(
        "/api/v1/auth/provision-student",
        json={
            "email": "no-engagement@example.com",
            "password": "password123",
            "full_name": "No Subject",
            "student_identifier": "S-norate",
            "institution_name": POC_INSTITUTION_NAME,
        },
    )
    assert provision.status_code == 200, provision.text
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "no-engagement@example.com", "password": "password123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    subject = seeded_db.scalar(select(Subject).where(Subject.code == POC_SUBJECT_CODE))
    assert subject is not None
    response = client.get(f"/api/v1/subjects/{subject.id}/engagement-rates", headers=headers)
    assert response.status_code == 404
