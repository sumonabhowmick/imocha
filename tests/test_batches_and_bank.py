"""Batches, CSV import, question bank and per-student randomization."""
from __future__ import annotations

from sqlalchemy import func, select

from app.models import Attempt, Candidate, Question
from tests.helpers import MCQ_BASICS, MCQ_EXCEPTIONS, MCQ_MULTI, create_assessment, create_candidate, csrf, setup_admin, start, student_session


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------
def test_batch_lifecycle_and_assignment(client, mailbox):
    headers = setup_admin(client)
    a = create_candidate(client, headers, name="Asha", email="asha@example.com")
    b = create_candidate(client, headers, name="Ravi", email="ravi@example.com")
    c = create_candidate(client, headers, name="Lee", email="lee@example.com")
    client.patch(f"/api/candidates/{c['id']}", json={"is_active": False}, headers=headers)

    batch = client.post("/api/batches", json={"name": "CSE 2026 - A"}, headers=headers).json()
    assert client.post("/api/batches", json={"name": "cse 2026 - a"}, headers=headers).status_code == 409
    detail = client.post(f"/api/batches/{batch['id']}/members", json={"candidate_ids": [a["id"], b["id"], c["id"]]}, headers=headers).json()
    assert detail["member_count"] == 3

    assessment = create_assessment(client, headers)
    res = client.post(f"/api/assessments/{assessment['id']}/assign-batch", json={"batch_id": batch["id"], "send_invitations": True}, headers=headers).json()
    assert len(res["assignment_ids"]) == 2  # inactive member skipped
    assert len(mailbox.to("asha@example.com")) == 1 and len(mailbox.to("ravi@example.com")) == 1
    assert mailbox.to("lee@example.com") == []

    listed = client.get(f"/api/candidates?batch_id={batch['id']}", headers=headers).json()
    assert {x["name"] for x in listed} == {"Asha", "Ravi", "Lee"}
    assert listed[0]["batches"] == [{"id": batch["id"], "name": "CSE 2026 - A"}]

    client.delete(f"/api/batches/{batch['id']}/members/{b['id']}", headers=headers)
    assert client.get(f"/api/batches/{batch['id']}", headers=headers).json()["member_count"] == 2
    assert client.delete(f"/api/batches/{batch['id']}", headers=headers).status_code == 204
    assert len(client.get("/api/candidates", headers=headers).json()) == 3  # candidates kept


def test_csv_import_preview_then_commit(client, db):
    headers = setup_admin(client)
    create_candidate(client, headers, name="Asha Rao", email="asha@example.com")
    batch = client.post("/api/batches", json={"name": "Imported"}, headers=headers).json()
    csv_text = (
        "Name,Email\n"
        "Asha Rao,ASHA@example.com\n"      # existing -> reused
        "Ravi Kumar,ravi@example.com\n"     # new
        "Ravi Again,ravi@example.com\n"     # duplicate in file
        "No Email,not-an-email\n"           # invalid
        ",blank@example.com\n"              # missing name
        "\n"
    )
    preview = client.post("/api/candidates/import", json={"csv_text": csv_text, "batch_id": batch["id"], "dry_run": True}, headers=headers).json()
    assert preview["summary"] == {"new": 1, "existing": 1, "duplicate": 1, "invalid": 2}
    assert db.scalar(select(func.count(Candidate.id))) == 1  # dry run saves nothing

    done = client.post("/api/candidates/import", json={"csv_text": csv_text, "batch_id": batch["id"], "dry_run": False}, headers=headers).json()
    assert len(done["created_ids"]) == 1
    assert db.scalar(select(func.count(Candidate.id))) == 2
    assert client.get(f"/api/batches/{batch['id']}", headers=headers).json()["member_count"] == 2


def test_csv_import_without_header(client):
    headers = setup_admin(client)
    res = client.post("/api/candidates/import", json={"csv_text": "Asha,asha@example.com\nRavi,ravi@example.com", "dry_run": False}, headers=headers)
    assert res.json()["summary"]["new"] == 2


# ---------------------------------------------------------------------------
# Question bank
# ---------------------------------------------------------------------------
def test_bank_crud_and_copy_into_draft(client, db):
    headers = setup_admin(client)
    created = client.post("/api/bank/questions", json={**MCQ_BASICS, "difficulty": "beginner"}, headers=headers)
    assert created.status_code == 201
    bank_id = created.json()["id"]
    assert client.post("/api/bank/questions", json={**MCQ_BASICS, "correct_answer": "Z"}, headers=headers).status_code == 422

    listing = client.get("/api/bank/questions?topic=basics", headers=headers).json()
    assert listing["total"] == 1 and listing["topics"] == [{"topic": "Basics", "count": 1}]

    draft = create_assessment(client, headers, questions=[], publish=False)
    out = client.post(f"/api/assessments/{draft['id']}/questions/from-bank", json={"bank_question_ids": [bank_id]}, headers=headers).json()
    assert out["question_count"] == 1
    # Editing the bank later does not change the copy.
    client.patch(f"/api/bank/questions/{bank_id}", json={"question_text": "Changed"}, headers=headers)
    assert db.scalar(select(Question.question_text).where(Question.assessment_id == draft["id"])) == MCQ_BASICS["question_text"]

    published = create_assessment(client, headers)
    res = client.post(f"/api/assessments/{published['id']}/questions/from-bank", json={"bank_question_ids": [bank_id]}, headers=headers)
    assert res.status_code == 409  # published questions are locked


def test_random_pick_and_save_to_bank(client):
    headers = setup_admin(client)
    for q in (MCQ_BASICS, MCQ_EXCEPTIONS, MCQ_MULTI):
        client.post("/api/bank/questions", json=q, headers=headers)
    draft = create_assessment(client, headers, questions=[], publish=False)
    out = client.post(f"/api/assessments/{draft['id']}/questions/random-from-bank", json={"count": 2}, headers=headers).json()
    assert out["question_count"] == 2
    again = client.post(f"/api/assessments/{draft['id']}/questions/random-from-bank", json={"count": 2}, headers=headers)
    assert again.status_code == 400  # only 1 unused question left

    question_id = out["questions"][0]["id"]
    saved = client.post(f"/api/questions/{question_id}/save-to-bank", json={"difficulty": "advanced"}, headers=headers)
    assert saved.status_code == 201 and saved.json()["difficulty"] == "advanced"


# ---------------------------------------------------------------------------
# Randomization
# ---------------------------------------------------------------------------
def test_shuffled_order_is_per_attempt_and_stable(client, make_client, mailbox, db):
    headers = setup_admin(client)
    many = [{**MCQ_BASICS, "question_text": f"Question {i}"} for i in range(8)]
    assessment = create_assessment(client, headers, questions=many, shuffle_questions=True, shuffle_options=True)
    candidate = create_candidate(client, headers)
    client.post(f"/api/assessments/{assessment['id']}/assign", json={"candidate_ids": [candidate["id"]], "send_invitations": True}, headers=headers)
    student = student_session(make_client, mailbox, candidate["email"])
    attempt_id = start(student).json()["attempt_id"]

    attempt = db.get(Attempt, attempt_id)
    assert sorted(attempt.question_order) == sorted(q["id"] for q in assessment["questions"])
    first = student.get(f"/api/student/attempts/{attempt_id}/questions").json()["questions"]
    second = student.get(f"/api/student/attempts/{attempt_id}/questions").json()["questions"]
    assert [q["id"] for q in first] == [q["id"] for q in second] == attempt.question_order
    assert [q["number"] for q in first] == list(range(1, 9))
    for q in first:
        assert [o["id"] for o in q["options"]] == attempt.option_orders[str(q["id"])]

    # Grading still uses option ids, so the shuffled display never affects the score.
    for q in first:
        student.put(f"/api/student/attempts/{attempt_id}/answers/{q['id']}", json={"selected": "B"}, headers=csrf(student))
    student.post(f"/api/student/attempts/{attempt_id}/submit", headers=csrf(student))
    result = client.get("/api/admin/results", headers=headers).json()["items"][0]
    assert result["percentage"] == 100.0


def test_unshuffled_assessment_keeps_author_order(client, make_client, mailbox, db):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    candidate = create_candidate(client, headers)
    client.post(f"/api/assessments/{assessment['id']}/assign", json={"candidate_ids": [candidate["id"]], "send_invitations": True}, headers=headers)
    student = student_session(make_client, mailbox, candidate["email"])
    attempt_id = start(student).json()["attempt_id"]
    assert db.get(Attempt, attempt_id).question_order is None
    qs = student.get(f"/api/student/attempts/{attempt_id}/questions").json()["questions"]
    assert [q["id"] for q in qs] == [q["id"] for q in assessment["questions"]]
