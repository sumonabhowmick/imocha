"""Student portal: passwordless sign-in (email + one-time code), the student's own
assessments, test history and results.

A student only ever sees their own data. The portal shows the STUDENT feedback report,
never the admin coaching report, and never correct answers.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import utcnow
from app.errors import AppError, not_found
from app.models import (
    Assessment,
    AssessmentAssignment,
    Attempt,
    AuthSession,
    Candidate,
    Feedback,
    LoginCode,
    Result,
)
from app.security import constant_time_equals, generate_passcode, keyed_hash, rate_limiter
from app.services import attempt_service, auth_service, email_service
from app.services.assessment_service import display_status

logger = logging.getLogger(__name__)

GENERIC_CODE_SENT = "If this email belongs to a registered candidate, a sign-in code has been sent to it."
INVALID_CODE = "The code is incorrect or has expired. Request a new code and try again."


def _hash_login_code(code_id: int, code: str) -> str:
    return keyed_hash(f"login-code:{code_id}", code)


# ---------------------------------------------------------------------------
# Sign-in with email + one-time code
# ---------------------------------------------------------------------------
def request_login_code(db: Session, email: str, client_ip: str) -> str:
    """Email a sign-in code. Always returns the same message, so the endpoint cannot be
    used to discover which emails are registered."""
    settings = get_settings()
    email = auth_service.normalize_email(email)
    if not rate_limiter.hit(f"portal-code-ip:{client_ip}", 10, 600) or not rate_limiter.hit(f"portal-code-email:{email}", 5, 600):
        raise AppError(429, "Too many requests. Please wait a few minutes and try again.", "rate_limited")
    candidate = db.scalar(select(Candidate).where(Candidate.email == email))
    if candidate is None or not candidate.is_active:
        return GENERIC_CODE_SENT
    now = utcnow()
    latest = db.scalar(
        select(LoginCode).where(LoginCode.candidate_id == candidate.id).order_by(LoginCode.created_at.desc()).limit(1)
    )
    if latest and (now - latest.created_at).total_seconds() < settings.passcode_resend_cooldown_seconds:
        return GENERIC_CODE_SENT  # cooldown: one code per minute
    # Older unused codes stop working.
    db.execute(
        update(LoginCode)
        .where(LoginCode.candidate_id == candidate.id, LoginCode.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    code = generate_passcode()
    login_code = LoginCode(
        candidate_id=candidate.id,
        code_hash="pending",
        expires_at=now + timedelta(minutes=settings.passcode_ttl_minutes),
    )
    db.add(login_code)
    db.flush()
    login_code.code_hash = _hash_login_code(login_code.id, code)
    db.commit()
    html, text = email_service._render(
        "login_code", candidate_name=candidate.name, passcode=code, passcode_ttl=settings.passcode_ttl_minutes
    )
    log = email_service.new_log(db, candidate.email, "login_code", "Your AssessFlow AI sign-in code")
    email_service.deliver(db, log, html, text)
    return GENERIC_CODE_SENT


def verify_login_code(db: Session, email: str, code: str, client_ip: str) -> tuple[Candidate, str, str]:
    """Check the code and open a portal session. Returns (candidate, session_token, csrf_token)."""
    settings = get_settings()
    if not rate_limiter.hit(f"portal-verify-ip:{client_ip}", 30, 600):
        raise AppError(429, "Too many attempts. Please wait a few minutes.", "rate_limited")
    candidate = db.scalar(select(Candidate).where(Candidate.email == auth_service.normalize_email(email)))
    if candidate is None or not candidate.is_active:
        raise AppError(400, INVALID_CODE, "invalid_code")
    login_code = db.scalar(
        select(LoginCode)
        .where(LoginCode.candidate_id == candidate.id, LoginCode.consumed_at.is_(None))
        .order_by(LoginCode.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    now = utcnow()
    if login_code is None or login_code.expires_at <= now:
        raise AppError(400, INVALID_CODE, "invalid_code")
    if not constant_time_equals(_hash_login_code(login_code.id, code), login_code.code_hash):
        login_code.failed_attempts += 1
        if login_code.failed_attempts >= settings.passcode_max_attempts:
            login_code.consumed_at = now  # burn the code; the student must request a new one
        db.commit()
        raise AppError(400, INVALID_CODE, "invalid_code")
    login_code.consumed_at = now
    token, csrf = auth_service.create_candidate_session(db, candidate.id)
    db.commit()
    return candidate, token, csrf


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------
def candidate_for_session(db: Session, session: AuthSession) -> Candidate:
    candidate_id = session.candidate_id
    if candidate_id is None and session.invitation_id:  # sessions created before portal support
        from app.models import Invitation

        inv = db.get(Invitation, session.invitation_id)
        candidate_id = inv.assignment.candidate_id if inv else None
    candidate = db.get(Candidate, candidate_id) if candidate_id else None
    if candidate is None or not candidate.is_active:
        raise AppError(403, "Your access has been disabled. Contact your administrator.", "forbidden")
    return candidate


def owned_assignment(db: Session, candidate: Candidate, assignment_id: int) -> AssessmentAssignment:
    """An assignment that belongs to this candidate; anything else looks 'not found'."""
    assignment = db.scalar(
        select(AssessmentAssignment).where(
            AssessmentAssignment.id == assignment_id, AssessmentAssignment.candidate_id == candidate.id
        )
    )
    if assignment is None or assignment.status == "revoked":
        raise not_found("Assessment")
    return assignment


def owned_attempt_assignment(db: Session, candidate: Candidate, attempt_id: int) -> AssessmentAssignment:
    attempt = db.get(Attempt, attempt_id)
    if attempt is None:
        raise not_found("Attempt")
    return owned_assignment(db, candidate, attempt.assignment_id)


# ---------------------------------------------------------------------------
# Portal views
# ---------------------------------------------------------------------------
def _latest_result(assignment: AssessmentAssignment) -> tuple[Attempt | None, Result | None]:
    attempts = sorted(assignment.attempts, key=lambda a: a.attempt_number)
    if not attempts:
        return None, None
    last = attempts[-1]
    return last, last.result


def assessments_for(db: Session, candidate: Candidate) -> list[dict]:
    """Every (non-revoked) assignment with what the student can do next."""
    now = utcnow()
    assignments = list(
        db.scalars(
            select(AssessmentAssignment)
            .where(AssessmentAssignment.candidate_id == candidate.id, AssessmentAssignment.status != "revoked")
            .options(selectinload(AssessmentAssignment.attempts).selectinload(Attempt.result))
        )
    )
    items = []
    for a in assignments:
        assessment = db.get(Assessment, a.assessment_id)
        if assessment.status == "draft":
            continue  # never show unpublished assessments to students
        in_progress = next((t for t in a.attempts if t.status == "in_progress"), None)
        if in_progress and attempt_service.finalize_if_expired(db, in_progress):
            db.commit()
            in_progress = None
        used = len(a.attempts)
        blocked = None if in_progress else attempt_service.start_blocked_reason(assessment, a, used, now)
        last_attempt, result = _latest_result(a)
        if in_progress:
            state = "in_progress"
        elif blocked is None:
            state = "open"
        elif assessment.access_start_at and now < assessment.access_start_at:
            state = "upcoming"
        elif used:
            state = "completed"
        else:
            state = "missed" if assessment.access_end_at and now >= assessment.access_end_at else "closed"
        items.append(
            {
                "assignment_id": a.id,
                "assessment_id": assessment.id,
                "title": assessment.title,
                "category": assessment.category,
                "difficulty": assessment.difficulty,
                "question_count": len(assessment.questions),
                "duration_minutes": assessment.duration_minutes,
                "access_start_at": assessment.access_start_at,
                "access_end_at": assessment.access_end_at,
                "proctoring_mode": assessment.proctoring_mode,
                "state": state,
                "can_start": in_progress is None and blocked is None,
                "start_blocked_reason": blocked,
                "in_progress_attempt_id": in_progress.id if in_progress else None,
                "attempts_used": used,
                "max_attempts": a.max_attempts,
                "latest_result": _result_summary(result) if result else None,
                "assessment_status": display_status(assessment, now),
                # When the student last dealt with it: their latest submission, otherwise when it closed.
                "last_activity_at": (last_attempt.submitted_at if last_attempt and last_attempt.submitted_at else None)
                or assessment.access_end_at,
            }
        )
    items.sort(key=lambda i: _portal_sort_key(i, now))
    return items


def _portal_sort_key(item: dict, now) -> tuple:
    """Available now: in-progress first, then whatever closes soonest. Upcoming: opens
    soonest first. Past (completed/closed/missed): most recent first."""
    far = now.replace(year=now.year + 100)
    state = item["state"]
    if state in ("in_progress", "open"):
        return (0, 0 if state == "in_progress" else 1, (item["access_end_at"] or far).timestamp())
    if state == "upcoming":
        return (1, 0, (item["access_start_at"] or far).timestamp())
    return (2, 0, -(item["last_activity_at"] or now.replace(year=1970)).timestamp())


def _result_summary(result: Result) -> dict:
    final = result.result_status == "final"
    return {
        "result_id": result.id,
        "result_status": result.result_status,
        "raw_score": float(result.raw_score) if final else None,
        "max_score": float(result.max_score),
        "percentage": float(result.percentage) if final and result.percentage is not None else None,
        "category": result.category if final else None,
    }


def history_for(db: Session, candidate: Candidate) -> list[dict]:
    rows = db.execute(
        select(Attempt, Assessment)
        .join(AssessmentAssignment, Attempt.assignment_id == AssessmentAssignment.id)
        .join(Assessment, Assessment.id == AssessmentAssignment.assessment_id)
        .where(AssessmentAssignment.candidate_id == candidate.id, Attempt.status == "submitted")
        .options(selectinload(Attempt.result))
        .order_by(Attempt.submitted_at.desc())
    ).all()
    return [
        {
            "attempt_id": attempt.id,
            "assessment_title": assessment.title,
            "attempt_number": attempt.attempt_number,
            "started_at": attempt.started_at,
            "submitted_at": attempt.submitted_at,
            "auto_submitted": attempt.auto_submitted,
            "result": _result_summary(attempt.result) if attempt.result else None,
        }
        for attempt, assessment in rows
    ]


def result_for(db: Session, candidate: Candidate, result_id: int) -> dict:
    """The student's own result: score, topic summary and their student feedback.
    Per-question correct answers are not shown (questions may be reused)."""
    from app.services import result_service

    result = db.get(Result, result_id)
    if result is None:
        raise not_found("Result")
    attempt = db.get(Attempt, result.attempt_id)
    owned_assignment(db, candidate, attempt.assignment_id)  # 404 unless it is theirs
    detail = result_service.result_detail(db, result_id)
    final = result.result_status == "final"
    topics: dict[str, dict] = {}
    for q in detail["questions"]:
        t = topics.setdefault(q["topic"] or "General", {"topic": q["topic"] or "General", "earned": 0.0, "possible": 0.0, "questions": 0})
        t["earned"] += q["awarded_points"] or 0.0
        t["possible"] += q["max_points"] or 0.0
        t["questions"] += 1
    feedback = db.scalar(select(Feedback).where(Feedback.result_id == result_id, Feedback.feedback_type == "student"))
    return {
        "result_id": result.id,
        "assessment_title": detail["assessment_title"],
        "submitted_at": detail["submitted_at"],
        "auto_submitted": detail["auto_submitted"],
        **_result_summary(result),
        "correct_count": detail["correct_count"] if final else None,
        "incorrect_count": detail["incorrect_count"] if final else None,
        "unanswered_count": detail["unanswered_count"],
        "topics": [
            {**t, "percent": round(t["earned"] / t["possible"] * 100, 1) if final and t["possible"] else None}
            for t in topics.values()
        ] if final else [],
        "feedback": feedback.content if feedback and feedback.generation_status == "completed" else None,
        "feedback_status": feedback.generation_status if feedback else ("pending" if final else "waiting_for_review"),
    }
