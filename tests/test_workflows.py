"""LangGraph workflow tests: the path each graph takes, idempotency, and resilience.

The `steps` trace returned by the graphs shows exactly which nodes ran, so these tests
pin down the routing described in app/workflows/submission_graph.py and
app/workflows/group_report_graph.py.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.models import Attempt, EmailLog, Feedback, Result
from app.services import grading_service
from app.workflows import assessment_workflow as wf
from tests.helpers import (
    ADMIN_EMAIL,
    MCQ_BASICS,
    TEXT_Q,
    answer,
    expire_attempt,
    full_flow,
    setup_admin,
    start,
    student_session,
    assign,
    create_assessment,
    create_candidate,
    submit,
)

ANSWERS = {0: {"selected": "B"}, 1: {"selected": "A"}}


def _count(db, model, *where) -> int:
    return db.scalar(select(func.count(model.id)).where(*where))


def _result_emails(mailbox, email="asha@example.com"):
    return [m for m in mailbox.to(email) if m["Subject"].startswith("Your results")]


def test_final_result_path_runs_feedback_in_parallel_then_emails(client, make_client, mailbox, fake_ai, db):
    headers = setup_admin(client)
    flow = full_flow(client, headers, make_client, mailbox, answers=ANSWERS)
    assert len(_result_emails(mailbox)) == 1

    # Re-run with forced feedback to inspect the full trace of a final result.
    state = wf.run_submission_workflow(flow["attempt_id"], force_feedback=True)
    steps = state["steps"]
    assert steps[:3] == ["load_attempt", "compute_result", "notify_admin_result"]  # already graded: no regrading
    assert set(steps[3:5]) == {"student_feedback", "admin_feedback"}  # same superstep (parallel)
    assert steps[5:] == ["review_feedback", "send_result_email"]
    assert state["result_status"] == "final"
    assert state["student_feedback_status"] == state["admin_feedback_status"] == "completed"
    assert state["result_email_status"] == "not_sent_or_already_sent"
    assert state["errors"] == []
    assert len(_result_emails(mailbox)) == 1


def test_first_run_trace_grades_before_scoring(client, make_client, mailbox, monkeypatch):
    seen = {}
    original = wf.run_submission_workflow

    def spy(attempt_id, force_feedback=False):
        seen["state"] = original(attempt_id, force_feedback)
        return seen["state"]

    monkeypatch.setattr(wf, "run_submission_workflow", spy)
    headers = setup_admin(client)
    full_flow(client, headers, make_client, mailbox, answers=ANSWERS)

    steps = seen["state"]["steps"]
    assert steps[:4] == ["load_attempt", "grade_objective", "compute_result", "notify_admin_result"]
    assert "ai_grade_text" not in steps  # no written answers
    assert seen["state"]["result_created"] is True


def test_provisional_result_stops_before_feedback(client, make_client, mailbox, fake_ai):
    headers = setup_admin(client)
    flow = full_flow(
        client, headers, make_client, mailbox, questions=[MCQ_BASICS, TEXT_Q], answers={0: {"selected": "B"}, 1: {"text": "x"}}
    )
    state = wf.run_submission_workflow(flow["attempt_id"])
    assert state["steps"] == ["load_attempt", "compute_result", "notify_admin_result"]
    assert state["result_status"] == "provisional"
    assert fake_ai.calls == []
    # The admin was told a result is ready (with answers awaiting review); the student was not emailed.
    ready = [m for m in mailbox.to(ADMIN_EMAIL) if m["Subject"].startswith("Result ready")]
    assert len(ready) == 1 and "awaiting review" in mailbox.text_of(ready[0])
    assert _result_emails(mailbox) == []


def test_ai_grade_text_node_runs_only_when_enabled(client, make_client, mailbox, fake_ai, settings, monkeypatch):
    monkeypatch.setattr(settings, "ai_text_grading_enabled", True)
    seen = {}
    original = wf.run_submission_workflow
    monkeypatch.setattr(wf, "run_submission_workflow", lambda a, force_feedback=False: seen.setdefault("s", original(a, force_feedback)))
    headers = setup_admin(client)
    full_flow(
        client, headers, make_client, mailbox, questions=[MCQ_BASICS, TEXT_Q], answers={0: {"selected": "B"}, 1: {"text": "wraps"}}
    )
    state = seen["s"]
    assert state["steps"][:4] == ["load_attempt", "grade_objective", "ai_grade_text", "compute_result"]
    assert state["ai_gradable_answers"] == 1 and state["ai_graded_answers"] == 1
    assert state["result_status"] == "final"


def test_feedback_failure_path_notifies_admin(client, make_client, mailbox, fake_ai):
    headers = setup_admin(client)
    fake_ai.set("student", RuntimeError("down"))
    flow = full_flow(client, headers, make_client, mailbox, answers=ANSWERS)

    state = wf.run_submission_workflow(flow["attempt_id"])
    assert state["steps"][-2:] == ["review_feedback", "notify_feedback_failed"]
    assert state["student_feedback_status"] == "failed"
    assert state["result_email_status"] == "not_sent"
    failed = [m for m in mailbox.to(ADMIN_EMAIL) if "feedback generation failed" in m["Subject"]]
    assert len(failed) == 1  # sent once, even though the workflow ran twice


def test_workflow_is_idempotent(client, make_client, mailbox, fake_ai, db):
    headers = setup_admin(client)
    flow = full_flow(client, headers, make_client, mailbox, answers=ANSWERS)
    attempt_id = flow["attempt_id"]

    for _ in range(3):
        wf.process_attempt(attempt_id)
    # Repeated submit requests are also safe.
    assert submit(flow["student"], attempt_id).json()["already_submitted"] is True

    assert _count(db, Result, Result.attempt_id == attempt_id) == 1
    result_id = db.scalar(select(Result.id).where(Result.attempt_id == attempt_id))
    assert _count(db, Feedback, Feedback.result_id == result_id) == 2
    assert _count(db, EmailLog, EmailLog.related_result_id == result_id, EmailLog.notification_type == "result") == 1
    assert _count(db, EmailLog, EmailLog.notification_type == "admin_result_ready") == 1
    assert len(_result_emails(mailbox)) == 1
    # Completed feedback was not regenerated by the repeat runs.
    assert len(fake_ai.calls_of("student")) == 1 and len(fake_ai.calls_of("admin")) == 1


def test_in_progress_attempt_is_not_graded(client, make_client, mailbox, db):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    candidate = create_candidate(client, headers)
    assign(client, headers, assessment["id"], candidate["id"])
    student = student_session(make_client, mailbox, candidate["email"])
    attempt_id = start(student).json()["attempt_id"]

    summary = wf.process_attempt(attempt_id)
    assert summary["result_id"] is None
    assert summary["errors"] and summary["errors"][0].startswith("load_attempt:")
    assert _count(db, Result, Result.attempt_id == attempt_id) == 0


def test_unexpected_node_error_is_contained_and_rerun_recovers(client, make_client, mailbox, monkeypatch, db):
    headers = setup_admin(client)
    original = grading_service.ensure_result

    def broken(db, attempt_id):
        raise ConnectionError("database connection lost: password=hunter2")

    monkeypatch.setattr(grading_service, "ensure_result", broken)
    flow = full_flow(client, headers, make_client, mailbox, answers=ANSWERS)
    assert flow["submit_response"].status_code == 200  # the student is unaffected
    attempt_id = flow["attempt_id"]
    assert _count(db, Result, Result.attempt_id == attempt_id) == 0

    summary = wf.process_attempt(attempt_id)
    assert summary["errors"] == ["compute_result: unexpected ConnectionError"]  # no exception text leaks

    # Restore only this function (monkeypatch.undo() would also remove conftest's fake SMTP).
    monkeypatch.setattr(grading_service, "ensure_result", original)
    # The sweeper finds submitted attempts without a result and finishes them.
    wf.sweep_once()
    assert _count(db, Result, Result.attempt_id == attempt_id) == 1
    assert len(_result_emails(mailbox)) == 1


def test_sweeper_auto_submits_expired_attempts_and_processes_them(client, make_client, mailbox, db):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    candidate = create_candidate(client, headers)
    assign(client, headers, assessment["id"], candidate["id"])
    student = student_session(make_client, mailbox, candidate["email"])
    attempt_id = start(student).json()["attempt_id"]
    answer(student, attempt_id, assessment["questions"][0]["id"], selected="B")
    expire_attempt(attempt_id)

    wf.sweep_once()

    attempt = db.get(Attempt, attempt_id)
    assert attempt.status == "submitted" and attempt.auto_submitted is True
    result = db.scalar(select(Result).where(Result.attempt_id == attempt_id))
    assert result.raw_score == 2 and result.unanswered_count == 1
    assert len(_result_emails(mailbox, candidate["email"])) == 1


def test_group_report_graph_paths(client, make_client, mailbox, fake_ai):
    headers = setup_admin(client)
    empty = create_assessment(client, headers)
    state = wf.run_group_report(empty["id"])
    assert state["steps"] == ["compute_statistics"] and state["status"] == "insufficient_data"

    flow = full_flow(client, headers, make_client, mailbox, answers=ANSWERS, email="lee@example.com", name="Lee")
    state = wf.run_group_report(flow["assessment"]["id"])
    assert state["steps"] == ["compute_statistics", "pseudonymize", "generate_summary", "map_candidate_refs", "save_report"]
    assert state["status"] == "completed"

    fake_ai.set("group", RuntimeError("down"))
    state = wf.run_group_report(flow["assessment"]["id"])
    assert state["steps"] == ["compute_statistics", "pseudonymize", "generate_summary", "save_report"]
    assert state["status"] == "failed"


def test_workflow_diagrams_describe_both_graphs():
    diagrams = wf.workflow_diagrams()
    for node in ("grade_objective", "ai_grade_text", "student_feedback", "admin_feedback", "send_result_email"):
        assert node in diagrams["submission"]
    for node in ("compute_statistics", "pseudonymize", "generate_summary", "save_report"):
        assert node in diagrams["group_report"]
