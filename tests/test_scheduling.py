from datetime import timedelta

from app.models import Assessment
from app.services.attempt_service import calculate_deadline
from tests.helpers import (
    answer,
    assign,
    create_assessment,
    create_candidate,
    expire_attempt,
    now,
    set_window,
    setup_admin,
    start,
    student_session,
    submit,
)


def ready_student(client, make_client, mailbox, **kwargs):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers, **kwargs)
    cand = create_candidate(client, headers)
    assign(client, headers, assessment["id"], cand["id"])
    return headers, assessment, student_session(make_client, mailbox, cand["email"])


def test_cannot_start_before_window(client, make_client, mailbox):
    _, _, student = ready_student(
        client, make_client, mailbox, start=now() + timedelta(hours=1), end=now() + timedelta(hours=4)
    )
    overview = student.get("/api/student/assessment").json()
    assert overview["can_start"] is False and "opens at" in overview["start_blocked_reason"]
    res = start(student)
    assert res.status_code == 403 and res.json()["code"] == "cannot_start"


def test_cannot_start_after_window(client, make_client, mailbox):
    _, assessment, student = ready_student(client, make_client, mailbox)
    set_window(assessment["id"], now() - timedelta(hours=3), now() - timedelta(minutes=1))
    res = start(student)
    assert res.status_code == 403 and "closed" in res.json()["detail"]


def test_archived_assessment_cannot_be_started(client, make_client, mailbox):
    headers, assessment, student = ready_student(client, make_client, mailbox)
    client.post(f"/api/assessments/{assessment['id']}/archive", headers=headers)
    overview = student.get("/api/student/assessment").json()
    assert overview["can_start"] is False
    res = start(student)
    assert res.status_code == 403 and "no longer available" in res.json()["detail"]


def test_draft_cannot_be_accessed(client, make_client, mailbox, db):
    headers = setup_admin(client)
    draft = create_assessment(client, headers, publish=False)
    cand = create_candidate(client, headers)
    assignment_id = assign(client, headers, draft["id"], cand["id"], invite=False)
    assert client.post(f"/api/assignments/{assignment_id}/invite", headers=headers).status_code == 409
    a = db.get(Assessment, draft["id"])
    assert a.status == "draft"


def test_deadline_calculation_policies():
    start_at = now()
    finish = Assessment(duration_minutes=45, completion_policy="finish_after_window",
                        access_start_at=start_at - timedelta(hours=2), access_end_at=start_at + timedelta(minutes=20))
    hard = Assessment(duration_minutes=45, completion_policy="hard_end_at_window",
                      access_start_at=start_at - timedelta(hours=2), access_end_at=start_at + timedelta(minutes=20))
    assert calculate_deadline(finish, start_at) == start_at + timedelta(minutes=45)
    assert calculate_deadline(hard, start_at) == start_at + timedelta(minutes=20)


def test_finish_after_window_gets_full_duration(client, make_client, mailbox):
    # Window closes in 20 minutes but the attempt keeps its full 45 minutes.
    _, _, student = ready_student(
        client, make_client, mailbox, start=now() - timedelta(hours=2), end=now() + timedelta(minutes=20)
    )
    res = start(student).json()
    assert 44 * 60 <= res["remaining_seconds"] <= 45 * 60


def test_hard_end_at_window_caps_deadline(client, make_client, mailbox):
    _, assessment, student = ready_student(
        client, make_client, mailbox, start=now() - timedelta(hours=2), end=now() + timedelta(minutes=20),
        completion_policy="hard_end_at_window",
    )
    res = start(student).json()
    assert 19 * 60 <= res["remaining_seconds"] <= 20 * 60


def test_server_records_start_and_deadline(client, make_client, mailbox):
    _, _, student = ready_student(client, make_client, mailbox, duration_minutes=30)
    before = now()
    res = start(student).json()
    from datetime import datetime

    started = datetime.fromisoformat(res["started_at"])
    deadline = datetime.fromisoformat(res["deadline_at"])
    assert started >= before - timedelta(seconds=2)
    assert deadline - started == timedelta(minutes=30)


def test_repeated_start_does_not_create_extra_attempts(client, make_client, mailbox):
    _, _, student = ready_student(client, make_client, mailbox)
    first = start(student).json()
    second = start(student).json()
    assert first["attempt_id"] == second["attempt_id"]
    assert student.get("/api/student/assessment").json()["attempts_used"] == 1


def test_max_attempts_enforced(client, make_client, mailbox):
    _, _, student = ready_student(client, make_client, mailbox)
    attempt_id = start(student).json()["attempt_id"]
    submit(student, attempt_id)
    res = start(student)
    assert res.status_code == 403 and "all permitted attempts" in res.json()["detail"]


def test_second_attempt_allowed_when_configured(client, make_client, mailbox):
    _, _, student = ready_student(client, make_client, mailbox, max_attempts=2)
    first = start(student).json()["attempt_id"]
    submit(student, first)
    second = start(student).json()
    assert second["attempt_number"] == 2 and second["attempt_id"] != first


def test_expired_attempt_rejects_late_answers_and_is_auto_submitted(client, make_client, mailbox):
    _, assessment, student = ready_student(client, make_client, mailbox)
    attempt_id = start(student).json()["attempt_id"]
    q = assessment["questions"][0]["id"]
    assert answer(student, attempt_id, q, selected="B").status_code == 200
    expire_attempt(attempt_id)
    late = answer(student, attempt_id, assessment["questions"][1]["id"], selected="B")
    assert late.status_code == 409 and late.json()["code"] == "attempt_closed"
    status = student.get(f"/api/student/attempts/{attempt_id}/status").json()
    assert status["status"] == "submitted" and status["auto_submitted"] is True
    # Submitting afterwards is idempotent and reports the auto-submission.
    res = submit(student, attempt_id).json()
    assert res["auto_submitted"] is True and res["already_submitted"] is True


def test_expired_attempt_grades_only_saved_answers(client, make_client, mailbox):
    headers, assessment, student = ready_student(client, make_client, mailbox)
    attempt_id = start(student).json()["attempt_id"]
    answer(student, attempt_id, assessment["questions"][0]["id"], selected="B")  # correct, 2 pts
    expire_attempt(attempt_id)
    submit(student, attempt_id)  # triggers processing
    results = client.get("/api/admin/results", headers=headers).json()["items"]
    assert len(results) == 1
    r = results[0]
    assert r["raw_score"] == 2 and r["max_score"] == 5 and r["unanswered_count"] == 1 and r["auto_submitted"] is True


def test_sweeper_finalizes_expired_attempts(client, make_client, mailbox):
    from app.workflows.assessment_workflow import sweep_once

    headers, _, student = ready_student(client, make_client, mailbox)
    attempt_id = start(student).json()["attempt_id"]
    expire_attempt(attempt_id)
    sweep_once()
    results = client.get("/api/admin/results", headers=headers).json()["items"]
    assert len(results) == 1 and results[0]["auto_submitted"] is True
