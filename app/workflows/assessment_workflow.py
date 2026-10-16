"""Public entry points for AssessFlow AI's LangGraph workflows.

* Submission workflow (submission_graph.py): grade -> result -> admin notice ->
  parallel student/admin feedback (LangChain) -> result email or failure notice.
* Group report workflow (group_report_graph.py): Python statistics -> pseudonymize ->
  LangChain summary -> map refs back -> save.

The API layer calls the functions below; they hide LangGraph details. Every graph node
is idempotent, so running a workflow twice for the same attempt (a duplicate submit,
the background sweeper, or an admin retry) never duplicates results or emails.
Graph nodes open their own database sessions because they run after the HTTP response
(and the two feedback nodes run in parallel threads).
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.errors import AppError, conflict, not_found
from app.models import EmailLog, Result
from app.services import attempt_service, email_service
from app.workflows.group_report_graph import build_group_report_graph
from app.workflows.submission_graph import build_submission_graph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Submission workflow
# ---------------------------------------------------------------------------
def run_submission_workflow(attempt_id: int, force_feedback: bool = False) -> dict:
    """Invoke the LangGraph submission workflow and return its final state."""
    state = build_submission_graph().invoke({"attempt_id": attempt_id, "force_feedback": force_feedback})
    level = logging.WARNING if state.get("errors") else logging.INFO
    logger.log(
        level,
        "Submission workflow for attempt %s: steps=%s result=%s/%s feedback=%s/%s email=%s errors=%s",
        attempt_id,
        state.get("steps"),
        state.get("result_id"),
        state.get("result_status"),
        state.get("student_feedback_status"),
        state.get("admin_feedback_status"),
        state.get("result_email_status"),
        state.get("errors"),
    )
    return state


def _summary(state: dict) -> dict:
    return {
        "result_id": state.get("result_id"),
        "result_status": state.get("result_status"),
        "feedback": {
            "student": state.get("student_feedback_status"),
            "admin": state.get("admin_feedback_status"),
        },
        "result_email": state.get("result_email_status"),
        "steps": state.get("steps", []),
        "errors": state.get("errors", []),
    }


def process_attempt(attempt_id: int) -> dict:
    """Full pipeline for one submitted attempt (FastAPI background task). Never raises."""
    try:
        return _summary(run_submission_workflow(attempt_id))
    except Exception:  # never crash the worker; saved data stays intact
        logger.exception("Submission workflow crashed for attempt %s", attempt_id)
        return {"errors": ["workflow crashed"]}


def _attempt_id_for_result(result_id: int) -> int:
    with SessionLocal() as db:
        attempt_id = db.scalar(select(Result.attempt_id).where(Result.id == result_id))
    if attempt_id is None:
        raise not_found("Result")
    return attempt_id


def retry_feedback(result_id: int, force: bool = False) -> dict:
    """Admin retry: re-run the workflow for this result. Completed steps are skipped;
    failed or pending feedback is regenerated (all feedback if `force`). The result
    email is still sent at most once."""
    return _summary(run_submission_workflow(_attempt_id_for_result(result_id), force_feedback=force))


def deliver_feedback_and_email(db: Session, result_id: int, force_feedback: bool = False) -> dict:
    """Generate feedback and send the result email for an existing result.
    (`db` is accepted for API compatibility; graph nodes use their own sessions.)"""
    return retry_feedback(result_id, force=force_feedback)


def after_manual_review(result_id: int) -> dict:
    """Run once a provisional result becomes final after admin review. Never raises."""
    try:
        return retry_feedback(result_id)
    except Exception:
        logger.exception("Post-review workflow failed for result %s", result_id)
        return {"errors": ["workflow crashed"]}


# ---------------------------------------------------------------------------
# Group report workflow
# ---------------------------------------------------------------------------
def run_group_report(assessment_id: int) -> dict:
    """Invoke the LangGraph group-report workflow and return its final state."""
    state = build_group_report_graph().invoke({"assessment_id": assessment_id})
    logger.info(
        "Group report workflow for assessment %s: steps=%s status=%s errors=%s",
        assessment_id,
        state.get("steps"),
        state.get("status"),
        state.get("errors"),
    )
    return state


# ---------------------------------------------------------------------------
# Email retries (no AI involved)
# ---------------------------------------------------------------------------
def retry_email(db: Session, log_id: int) -> EmailLog:
    """Admin-initiated retry of a failed notification.

    Invitation/passcode emails cannot simply be re-sent because raw codes are never
    stored; instead a fresh invitation (new link + new code) is issued and the old one
    is revoked. Result emails are re-rendered from the database (still at most one)."""
    from app.services import invitation_service

    log = db.get(EmailLog, log_id)
    if log is None:
        raise not_found("Email log")
    if log.status in ("sent", "outbox"):
        raise conflict("This email was already delivered.")
    if log.notification_type in ("invitation", "passcode"):
        if log.related_assignment_id is None:
            raise conflict("The related assignment no longer exists.")
        log.notification_type = "invitation"
        return invitation_service.issue_invitation(db, log.related_assignment_id, log=log).email_log
    if log.notification_type == "result":
        if log.related_result_id is None:
            raise conflict("The related result no longer exists.")
        sent = email_service.send_result_email(db, log.related_result_id, allow_without_feedback=True)
        return sent or db.get(EmailLog, log_id)
    return email_service.resend_admin_notice(db, log)


# ---------------------------------------------------------------------------
# Background sweeper
# ---------------------------------------------------------------------------
def sweep_once() -> None:
    """Auto-finalize expired attempts and run the workflow for submitted attempts that
    have no result yet (e.g. the server stopped mid-processing)."""
    with SessionLocal() as db:
        attempt_service.finalize_expired_attempts(db)
        pending = attempt_service.unprocessed_submitted_attempts(db)
    for attempt_id in pending:
        process_attempt(attempt_id)


class Sweeper:
    """Tiny background thread (no message broker needed for this project size)."""

    def __init__(self, interval_seconds: int) -> None:
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.interval <= 0:
            return
        self._thread = threading.Thread(target=self._run, name="assessflow-sweeper", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                sweep_once()
            except Exception:
                logger.exception("Sweeper iteration failed")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Diagrams (for the dashboard / README)
# ---------------------------------------------------------------------------
def workflow_diagrams() -> dict[str, str]:
    """Mermaid diagrams generated by LangGraph from the compiled graphs."""
    return {
        "submission": build_submission_graph().get_graph().draw_mermaid(),
        "group_report": build_group_report_graph().get_graph().draw_mermaid(),
    }


def ensure_group_report_ok(state: dict) -> None:
    """Translate a group-report run into API errors where appropriate."""
    if state.get("status") == "insufficient_data":
        raise AppError(400, "No final results yet; a group report needs at least one completed, graded attempt.", "bad_request")
    if state.get("errors"):
        raise AppError(500, "Group report generation failed. Please try again.", "workflow_failed")
