"""Student assessment sessions: starting attempts, delivering questions, saving answers,
submitting, and auto-finalizing expired attempts.

The server is the only authority on time. Browser timers are a display aid only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import utcnow
from app.dependencies import StudentContext
from app.errors import AppError, bad_request, conflict, not_found
from app.models import Answer, Assessment, AssessmentAssignment, Attempt, Question
from app.services.auth_service import extend_session
from app.services.email_service import format_local

logger = logging.getLogger(__name__)

SESSION_GRACE = timedelta(minutes=10)


# ---------------------------------------------------------------------------
# Time rules
# ---------------------------------------------------------------------------
def calculate_deadline(assessment: Assessment, started_at: datetime) -> datetime:
    """Deadline = start + duration; with hard_end_at_window it is capped at window end."""
    deadline = started_at + timedelta(minutes=assessment.duration_minutes)
    if assessment.completion_policy == "hard_end_at_window" and assessment.access_end_at:
        deadline = min(deadline, assessment.access_end_at)
    return deadline


def remaining_seconds(attempt: Attempt, now: datetime | None = None) -> int:
    now = now or utcnow()
    if attempt.status != "in_progress":
        return 0
    return max(0, int((attempt.deadline_at - now).total_seconds()))


def start_blocked_reason(
    assessment: Assessment, assignment: AssessmentAssignment, attempts_used: int, now: datetime
) -> str | None:
    """Why a new attempt cannot start right now (None = it can)."""
    if assessment.status == "draft":
        return "This assessment has not been published yet."
    if assessment.status == "archived":
        return "This assessment is no longer available."
    if assignment.status == "revoked":
        return "This assessment is no longer assigned to you."
    if not assessment.access_start_at or not assessment.access_end_at:
        return "This assessment has not been scheduled yet."
    if now < assessment.access_start_at:
        return f"The assessment opens at {format_local(assessment.access_start_at)}."
    if now >= assessment.access_end_at:
        return "The access window for this assessment has closed."
    if attempts_used >= assignment.max_attempts:
        return "You have used all permitted attempts for this assessment."
    if assessment.completion_policy == "hard_end_at_window" and calculate_deadline(assessment, now) <= now:
        return "The access window for this assessment has closed."
    return None


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------
def _finalize(attempt: Attempt, now: datetime, auto: bool) -> None:
    attempt.status = "submitted"
    attempt.auto_submitted = auto
    # An auto-finalized attempt is officially submitted at its deadline.
    attempt.submitted_at = min(now, attempt.deadline_at) if auto else now
    assignment = attempt.assignment
    if assignment.status != "revoked":
        assignment.status = "completed"


def finalize_if_expired(db: Session, attempt: Attempt) -> bool:
    """Auto-submit an in-progress attempt whose deadline has passed. Caller commits."""
    if attempt.status == "in_progress" and utcnow() >= attempt.deadline_at:
        _finalize(attempt, utcnow(), auto=True)
        logger.info("Attempt %s auto-finalized at deadline", attempt.id)
        return True
    return False


def finalize_expired_attempts(db: Session) -> list[int]:
    """Background sweep: auto-finalize every expired in-progress attempt."""
    expired = list(
        db.scalars(
            select(Attempt)
            .where(Attempt.status == "in_progress", Attempt.deadline_at <= utcnow())
            .with_for_update(skip_locked=True)
        )
    )
    for attempt in expired:
        _finalize(attempt, utcnow(), auto=True)
    db.commit()
    return [a.id for a in expired]


# ---------------------------------------------------------------------------
# Student operations
# ---------------------------------------------------------------------------
def _attempts_used(db: Session, assignment_id: int) -> int:
    return db.scalar(select(func.count(Attempt.id)).where(Attempt.assignment_id == assignment_id)) or 0


def _in_progress(db: Session, assignment_id: int) -> Attempt | None:
    return db.scalar(
        select(Attempt).where(Attempt.assignment_id == assignment_id, Attempt.status == "in_progress")
    )


def overview(db: Session, ctx: StudentContext) -> tuple[dict, list[int]]:
    """Assessment landing information. Returns (data, ids_of_attempts_just_finalized)."""
    settings = get_settings()
    finalized = []
    current = _in_progress(db, ctx.assignment.id)
    if current and finalize_if_expired(db, current):
        db.commit()
        finalized.append(current.id)
        current = None
    now = utcnow()
    used = _attempts_used(db, ctx.assignment.id)
    assessment = ctx.assessment
    reason = None if current else start_blocked_reason(assessment, ctx.assignment, used, now)
    return (
        {
            "candidate_name": ctx.candidate.name,
            "title": assessment.title,
            "description": assessment.description,
            "instructions": assessment.instructions,
            "category": assessment.category,
            "difficulty": assessment.difficulty,
            "question_count": len(assessment.questions),
            "total_points": float(sum(q.points for q in assessment.questions)),
            "duration_minutes": assessment.duration_minutes,
            "access_start_at": assessment.access_start_at,
            "access_end_at": assessment.access_end_at,
            "timezone": settings.app_timezone,
            "access_start_display": format_local(assessment.access_start_at) if assessment.access_start_at else None,
            "access_end_display": format_local(assessment.access_end_at) if assessment.access_end_at else None,
            "completion_policy": assessment.completion_policy,
            "attempts_used": used,
            "max_attempts": ctx.assignment.max_attempts,
            "can_start": current is None and reason is None,
            "start_blocked_reason": reason,
            "in_progress_attempt_id": current.id if current else None,
            "server_time": now,
        },
        finalized,
    )


def start_attempt(db: Session, ctx: StudentContext) -> tuple[Attempt, bool]:
    """Start (or resume) the student's attempt. Idempotent: repeated calls return the
    attempt already in progress instead of creating another. Returns (attempt, created)."""
    # Serialize concurrent start requests for this assignment.
    db.execute(
        select(AssessmentAssignment.id).where(AssessmentAssignment.id == ctx.assignment.id).with_for_update()
    )
    current = _in_progress(db, ctx.assignment.id)
    if current:
        if not finalize_if_expired(db, current):
            return current, False
        db.commit()
    now = utcnow()
    used = _attempts_used(db, ctx.assignment.id)
    reason = start_blocked_reason(ctx.assessment, ctx.assignment, used, now)
    if reason:
        raise AppError(403, reason, "cannot_start")

    attempt = Attempt(
        assignment_id=ctx.assignment.id,
        attempt_number=used + 1,
        started_at=now,
        deadline_at=calculate_deadline(ctx.assessment, now),
        status="in_progress",
    )
    db.add(attempt)
    ctx.assignment.status = "in_progress"
    extend_session(ctx.session, attempt.deadline_at + SESSION_GRACE)
    try:
        db.commit()
    except IntegrityError:  # a parallel request won the race; return its attempt
        db.rollback()
        existing = _in_progress(db, ctx.assignment.id)
        if existing:
            return existing, False
        raise conflict("Could not start the attempt. Please reload the page.")
    logger.info("Attempt %s started (assignment %s)", attempt.id, ctx.assignment.id)
    return attempt, True


def get_owned_attempt(db: Session, ctx: StudentContext, attempt_id: int, for_update: bool = False) -> Attempt:
    """Fetch an attempt only if it belongs to the session's assignment (prevents IDOR).
    Another student's attempt id looks exactly like a missing one."""
    stmt = select(Attempt).where(Attempt.id == attempt_id, Attempt.assignment_id == ctx.assignment.id)
    if for_update:
        stmt = stmt.with_for_update()
    attempt = db.scalar(stmt)
    if not attempt:
        raise not_found("Attempt")
    return attempt


def questions_for_attempt(db: Session, ctx: StudentContext, attempt_id: int) -> dict:
    """Student view of the questions: never includes correct answers, rubrics or explanations."""
    attempt = get_owned_attempt(db, ctx, attempt_id)
    if finalize_if_expired(db, attempt):
        db.commit()
        raise AppError(409, "Time is up. Your saved answers were submitted automatically.", "attempt_closed")
    if attempt.status != "in_progress":
        raise AppError(409, "This attempt has already been submitted.", "attempt_closed")
    saved = {a.question_id: a.answer_json for a in db.scalars(select(Answer).where(Answer.attempt_id == attempt.id))}
    questions = []
    for number, q in enumerate(ctx.assessment.questions, start=1):
        questions.append(
            {
                "id": q.id,
                "number": number,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "points": float(q.points),
                # Only id + text of each option; never the correct answer.
                "options": [{"id": o["id"], "text": o["text"]} for o in q.options_json] if q.options_json else None,
                "saved_answer": saved.get(q.id),
            }
        )
    return {
        "attempt_id": attempt.id,
        "attempt_number": attempt.attempt_number,
        "status": attempt.status,
        "started_at": attempt.started_at,
        "deadline_at": attempt.deadline_at,
        "remaining_seconds": remaining_seconds(attempt),
        "title": ctx.assessment.title,
        "questions": questions,
    }


def _normalize_answer(question: Question, selected, text) -> dict | None:
    """Validate an answer's format for the question type; None means 'cleared'."""
    qtype = question.question_type
    option_ids = {o["id"] for o in (question.options_json or [])}
    if qtype == "mcq_single":
        if selected is None:
            return None
        if not isinstance(selected, str) or selected not in option_ids:
            raise bad_request("Select one of the listed options.", "invalid_answer")
        return {"selected": selected}
    if qtype == "mcq_multi":
        if selected is None or selected == []:
            return None
        if (
            not isinstance(selected, list)
            or len(set(selected)) != len(selected)
            or any(not isinstance(s, str) or s not in option_ids for s in selected)
        ):
            raise bad_request("Select one or more of the listed options.", "invalid_answer")
        return {"selected": sorted(selected)}
    # text / coding
    if selected is not None:
        raise bad_request("This question expects a written answer.", "invalid_answer")
    if text is None or not text.strip():
        return None
    return {"text": text}


def save_answer(db: Session, ctx: StudentContext, attempt_id: int, question_id: int, selected, text) -> Attempt:
    attempt = get_owned_attempt(db, ctx, attempt_id, for_update=True)
    if finalize_if_expired(db, attempt):
        db.commit()
        raise AppError(409, "Time is up. This answer was not saved; your earlier answers were submitted.", "attempt_closed")
    if attempt.status != "in_progress":
        raise AppError(409, "This attempt has already been submitted.", "attempt_closed")
    question = db.get(Question, question_id)
    if not question or question.assessment_id != ctx.assessment.id:
        raise not_found("Question")
    answer_json = _normalize_answer(question, selected, text)
    stmt = insert(Answer).values(
        attempt_id=attempt.id,
        question_id=question.id,
        answer_json=answer_json,
        max_points=question.points,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_answers_attempt_question",
        set_={"answer_json": stmt.excluded.answer_json, "updated_at": utcnow()},
    )
    db.execute(stmt)
    db.commit()
    return attempt


def submit_attempt(db: Session, ctx: StudentContext, attempt_id: int) -> tuple[Attempt, bool]:
    """Submit once. Repeated submits return the existing submission (idempotent).
    Returns (attempt, newly_submitted)."""
    attempt = get_owned_attempt(db, ctx, attempt_id, for_update=True)
    if attempt.status == "submitted":
        db.commit()  # release the row lock taken above before any background processing
        return attempt, False
    if finalize_if_expired(db, attempt):
        db.commit()
        return attempt, True
    _finalize(attempt, utcnow(), auto=False)
    db.commit()
    logger.info("Attempt %s submitted", attempt.id)
    return attempt, True


def unprocessed_submitted_attempts(db: Session) -> list[int]:
    """Submitted attempts that have no result yet (e.g. the server stopped mid-processing)."""
    from app.models import Result

    stmt = (
        select(Attempt.id)
        .outerjoin(Result, Result.attempt_id == Attempt.id)
        .where(Attempt.status == "submitted", Result.id.is_(None))
    )
    return list(db.scalars(stmt))
