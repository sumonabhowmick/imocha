"""Reusable test helpers: build admins, assessments, candidates and student sessions
through the real API (so every helper also exercises authorization)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.database import SessionLocal
from app.main import app
from app.models import Assessment, Attempt, Invitation
from app.services.auth_service import create_admin

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "Correct-Horse-42!"


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def make_admin(email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD, name: str = "Test Admin"):
    with SessionLocal() as db:
        return create_admin(db, name, email, password)


def admin_headers(client: TestClient, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD) -> dict:
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def setup_admin(client: TestClient) -> dict:
    make_admin()
    return admin_headers(client)


MCQ_BASICS = {
    "question_text": "What does len([1, 2, 3]) return?",
    "question_type": "mcq_single",
    "topic": "Basics",
    "points": 2,
    "options": [{"id": "A", "text": "2"}, {"id": "B", "text": "3"}, {"id": "C", "text": "4"}],
    "correct_answer": "B",
}
MCQ_EXCEPTIONS = {
    "question_text": "Which keyword handles an exception?",
    "question_type": "mcq_single",
    "topic": "Exceptions",
    "points": 3,
    "options": [{"id": "A", "text": "catch"}, {"id": "B", "text": "except"}, {"id": "C", "text": "rescue"}],
    "correct_answer": "B",
}
MCQ_MULTI = {
    "question_text": "Which are mutable types?",
    "question_type": "mcq_multi",
    "topic": "Data types",
    "points": 1,
    "options": [{"id": "A", "text": "list"}, {"id": "B", "text": "tuple"}, {"id": "C", "text": "dict"}],
    "correct_answer": ["A", "C"],
}
TEXT_Q = {
    "question_text": "Explain what a Python decorator is.",
    "question_type": "text",
    "topic": "Functions",
    "points": 4,
    "grading_rubric": "1 pt: wraps a function. 1 pt: returns a new function. 1 pt: @ syntax. 1 pt: example use.",
}


def create_assessment(
    client: TestClient,
    headers: dict,
    questions: list[dict] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    publish: bool = True,
    **overrides,
) -> dict:
    """Create (and by default publish) an assessment whose window is open now."""
    start = start or now() - timedelta(hours=1)
    end = end or now() + timedelta(hours=2)
    body = {
        "title": overrides.pop("title", "Python Developer Assessment"),
        "category": "Python",
        "duration_minutes": overrides.pop("duration_minutes", 45),
        "access_start_at": iso(start),
        "access_end_at": iso(end),
        **overrides,
    }
    res = client.post("/api/assessments", json=body, headers=headers)
    assert res.status_code == 201, res.text
    assessment = res.json()
    for q in questions if questions is not None else [MCQ_BASICS, MCQ_EXCEPTIONS]:
        r = client.post(f"/api/assessments/{assessment['id']}/questions", json=q, headers=headers)
        assert r.status_code == 201, r.text
    if publish:
        r = client.post(f"/api/assessments/{assessment['id']}/publish", json={}, headers=headers)
        assert r.status_code == 200, r.text
    return client.get(f"/api/assessments/{assessment['id']}", headers=headers).json()


def create_candidate(client: TestClient, headers: dict, name: str = "Asha Rao", email: str = "asha@example.com") -> dict:
    res = client.post("/api/candidates", json={"name": name, "email": email}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def assign(client: TestClient, headers: dict, assessment_id: int, candidate_id: int, invite: bool = True, **extra) -> int:
    res = client.post(
        f"/api/assessments/{assessment_id}/assign",
        json={"candidate_ids": [candidate_id], "send_invitations": invite, **extra},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()["assignment_ids"][0]


def verify(student: TestClient, token: str, passcode: str):
    return student.post("/api/invitations/verify", json={"token": token, "passcode": passcode})


def csrf(student: TestClient) -> dict:
    return {"X-CSRF-Token": student.cookies.get("af_csrf")}


def student_session(make_client, mailbox, email: str) -> TestClient:
    """A verified student browser for the latest invitation sent to `email`."""
    student = make_client()
    token, passcode = mailbox.credentials_for(email)
    res = verify(student, token, passcode)
    assert res.status_code == 200, res.text
    return student


def start(student: TestClient):
    return student.post("/api/student/attempts", headers=csrf(student))


def answer(student: TestClient, attempt_id: int, question_id: int, **body):
    return student.put(f"/api/student/attempts/{attempt_id}/answers/{question_id}", json=body, headers=csrf(student))


def submit(student: TestClient, attempt_id: int):
    return student.post(f"/api/student/attempts/{attempt_id}/submit", headers=csrf(student))


def full_flow(client, headers, make_client, mailbox, questions=None, answers=None, email="asha@example.com", name="Asha Rao"):
    """Create -> assign -> invite -> verify -> start -> answer -> submit.

    `answers` maps question index -> answer body (e.g. {0: {"selected": "B"}}).
    Returns dict(assessment, candidate, assignment_id, student, attempt_id, submit_response).
    """
    assessment = create_assessment(client, headers, questions)
    candidate = create_candidate(client, headers, name=name, email=email)
    assignment_id = assign(client, headers, assessment["id"], candidate["id"])
    student = student_session(make_client, mailbox, email)
    attempt_id = start(student).json()["attempt_id"]
    for index, body in (answers or {}).items():
        res = answer(student, attempt_id, assessment["questions"][index]["id"], **body)
        assert res.status_code == 200, res.text
    submitted = submit(student, attempt_id)
    return {
        "assessment": assessment,
        "candidate": candidate,
        "assignment_id": assignment_id,
        "student": student,
        "attempt_id": attempt_id,
        "submit_response": submitted,
    }


# --- direct DB adjustments for time-based scenarios ------------------------------
def set_window(assessment_id: int, start: datetime, end: datetime) -> None:
    with SessionLocal() as db:
        db.execute(update(Assessment).where(Assessment.id == assessment_id).values(access_start_at=start, access_end_at=end))
        db.commit()


def expire_attempt(attempt_id: int) -> None:
    """Move an attempt's deadline into the past (as if time ran out)."""
    with SessionLocal() as db:
        db.execute(
            update(Attempt)
            .where(Attempt.id == attempt_id)
            .values(started_at=now() - timedelta(hours=2), deadline_at=now() - timedelta(seconds=1))
        )
        db.commit()


def update_invitation(assignment_id: int, **values) -> None:
    with SessionLocal() as db:
        db.execute(update(Invitation).where(Invitation.assignment_id == assignment_id).values(**values))
        db.commit()


__all__ = ["app"]
