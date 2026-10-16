"""Grading: deterministic objective scoring, optional AI-assisted text scoring, and
transactional, idempotent result calculation.

Grading an attempt twice never creates a second result (unique attempt_id), and the
official score is always computed here in Python with Decimal arithmetic.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import utcnow
from app.errors import bad_request, not_found
from app.models import Answer, Assessment, AssessmentAssignment, Attempt, Question, Result, User
from app.models.question import OBJECTIVE_TYPES
from app.services.ai_service import AIServiceError, ai_service
from app.services.assessment_service import category_scale_for
from app.services.scoring import calculate_percentage, categorize

logger = logging.getLogger(__name__)
ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Objective grading (pure functions)
# ---------------------------------------------------------------------------
def grade_objective(question: Question, answer_json: dict | None) -> tuple[Decimal, bool]:
    """Return (points_awarded, is_correct). Unanswered = 0 points (default policy).
    Multiple-select questions are all-or-nothing: the selected set must match exactly."""
    points = Decimal(question.points)
    if not answer_json or answer_json.get("selected") in (None, [], ""):
        return ZERO, False
    selected = answer_json["selected"]
    if question.question_type == "mcq_single":
        correct = isinstance(selected, str) and selected == question.correct_answer
    elif question.question_type == "mcq_multi":
        correct = isinstance(selected, list) and set(selected) == set(question.correct_answer or [])
    else:
        raise ValueError("grade_objective only handles multiple-choice questions")
    return (points if correct else ZERO), correct


def _is_answered(answer: Answer) -> bool:
    data = answer.answer_json
    if not data:
        return False
    if "text" in data:
        return bool(str(data["text"]).strip())
    return data.get("selected") not in (None, [], "")


# ---------------------------------------------------------------------------
# Attempt grading
# ---------------------------------------------------------------------------
def _load_attempt(db: Session, attempt_id: int, for_update: bool = False) -> Attempt:
    stmt = select(Attempt).where(Attempt.id == attempt_id)
    if for_update:
        stmt = stmt.with_for_update()
    attempt = db.scalar(stmt)
    if not attempt:
        raise not_found("Attempt")
    return attempt


def _assessment_for(db: Session, attempt: Attempt) -> Assessment:
    assignment = db.get(AssessmentAssignment, attempt.assignment_id)
    return db.scalar(
        select(Assessment).where(Assessment.id == assignment.assessment_id).options(selectinload(Assessment.questions))
    )


def ai_text_grading_enabled() -> bool:
    settings = get_settings()
    return settings.ai_text_grading_enabled and settings.ai_configured


def count_ai_gradable(db: Session, attempt_id: int) -> int:
    """Written answers that are awaiting grading and have a rubric the AI can use."""
    return sum(
        1
        for a in db.scalars(
            select(Answer)
            .where(Answer.attempt_id == attempt_id, Answer.grading_method == "pending_review")
            .options(selectinload(Answer.question))
        )
        if a.question.grading_rubric.strip()
    )


def prepare_answers(db: Session, attempt_id: int) -> None:
    """Step 1 (one transaction): create rows for unanswered questions and grade every
    objective answer. Text answers are marked pending_review for the next step."""
    attempt = _load_attempt(db, attempt_id, for_update=True)
    assessment = _assessment_for(db, attempt)
    answers = {a.question_id: a for a in db.scalars(select(Answer).where(Answer.attempt_id == attempt.id))}
    now = utcnow()
    for question in assessment.questions:
        answer = answers.get(question.id)
        if answer is None:
            answer = Answer(attempt_id=attempt.id, question_id=question.id, answer_json=None)
            db.add(answer)
        if answer.graded_at is not None or answer.grading_method == "pending_review":
            continue  # already processed (idempotent)
        answer.max_points = question.points
        if not _is_answered(answer):
            answer.awarded_points, answer.is_correct = ZERO, False
            answer.grading_method, answer.graded_at = "objective", now
        elif question.question_type in OBJECTIVE_TYPES:
            answer.awarded_points, answer.is_correct = grade_objective(question, answer.answer_json)
            answer.grading_method, answer.graded_at = "objective", now
        else:
            answer.grading_method = "pending_review"
            answer.needs_review = True
    db.commit()


def ai_grade_text_answers(db: Session, attempt_id: int) -> int:
    """Step 2 (optional): AI-assisted grading of written answers that have a rubric,
    using the LangChain text-grading chain. Returns how many answers were AI-graded.

    The model only suggests a score; Python enforces 0 <= score <= max_points, and
    low-confidence or out-of-range suggestions stay flagged for admin review."""
    if not ai_text_grading_enabled():
        return 0
    graded = 0
    pending = list(
        db.scalars(
            select(Answer)
            .where(Answer.attempt_id == attempt_id, Answer.grading_method == "pending_review")
            .options(selectinload(Answer.question))
        )
    )
    for answer in pending:
        question = answer.question
        if not question.grading_rubric.strip():
            continue  # no rubric = unsupported; stays with the admin for manual review
        max_points = Decimal(question.points)
        try:
            grade = ai_service.grade_text_answer(
                question.question_text,
                question.grading_rubric,
                question.correct_answer if isinstance(question.correct_answer, str) else None,
                str(answer.answer_json.get("text", "")),
                float(max_points),
            )
        except AIServiceError as exc:
            logger.warning("AI grading failed for answer %s: %s", answer.id, exc)
            continue  # remains pending_review
        suggested = Decimal(str(grade.awarded_points)).quantize(Decimal("0.01"))
        needs_review = grade.needs_review or grade.confidence == "low"
        if suggested < ZERO or suggested > max_points:
            needs_review = True
            suggested = min(max(suggested, ZERO), max_points)
        answer.awarded_points = suggested
        answer.is_correct = suggested == max_points and max_points > 0
        answer.grading_method = "ai_assisted"
        answer.grading_rationale = grade.rationale
        answer.grading_model = ai_service.model_name
        answer.needs_review = needs_review
        answer.graded_at = utcnow()
        db.commit()
        graded += 1
    return graded


def _compute_result_values(db: Session, attempt: Attempt) -> dict:
    assessment = _assessment_for(db, attempt)
    answers = list(db.scalars(select(Answer).where(Answer.attempt_id == attempt.id)))
    raw = sum((a.awarded_points or ZERO for a in answers), ZERO)
    max_score = sum((Decimal(a.max_points) for a in answers), ZERO)
    pending = sum(1 for a in answers if a.needs_review or a.awarded_points is None)
    unanswered = sum(1 for a in answers if not _is_answered(a))
    correct = sum(1 for a in answers if _is_answered(a) and a.is_correct and not a.needs_review)
    incorrect = sum(
        1 for a in answers if _is_answered(a) and a.is_correct is False and not a.needs_review and a.awarded_points is not None
    )
    methods = {a.grading_method for a in answers if a.grading_method}
    if not methods or methods == {"objective"}:
        method = "objective"
    elif len(methods - {"objective"}) == 1:
        method = next(iter(methods - {"objective"}))
        method = "mixed" if method == "pending_review" else method
    else:
        method = "mixed"
    status = "provisional" if pending else "final"
    percentage = calculate_percentage(raw, max_score)
    category = categorize(percentage, category_scale_for(assessment)) if status == "final" else None
    return {
        "raw_score": raw,
        "max_score": max_score,
        "percentage": percentage,
        "category": category,
        "correct_count": correct,
        "incorrect_count": incorrect,
        "unanswered_count": unanswered,
        "pending_review_count": pending,
        "grading_method": method,
        "result_status": status,
    }


def _upsert_result(db: Session, attempt_id: int) -> Result:
    """Step 3 (one transaction, attempt row locked): create or refresh the single result."""
    attempt = _load_attempt(db, attempt_id, for_update=True)
    values = _compute_result_values(db, attempt)
    result = db.scalar(select(Result).where(Result.attempt_id == attempt.id))
    if result is None:
        result = Result(attempt_id=attempt.id, calculated_at=utcnow(), **values)
        db.add(result)
    else:
        for key, value in values.items():
            setattr(result, key, value)
        result.calculated_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        result = db.scalar(select(Result).where(Result.attempt_id == attempt_id))
    return result


def get_result_for_attempt(db: Session, attempt_id: int) -> Result | None:
    return db.scalar(select(Result).where(Result.attempt_id == attempt_id))


def ensure_result(db: Session, attempt_id: int) -> tuple[Result, bool]:
    """Step 3: create the official result if it does not exist yet.
    Returns (result, created). An existing result is never recalculated here."""
    existing = get_result_for_attempt(db, attempt_id)
    if existing:
        return existing, False
    result = _upsert_result(db, attempt_id)
    logger.info("Attempt %s graded: result %s (%s)", attempt_id, result.id, result.result_status)
    return result, True


def require_submitted(db: Session, attempt_id: int) -> Attempt:
    attempt = _load_attempt(db, attempt_id)
    if attempt.status != "submitted":
        raise bad_request("Only submitted attempts can be graded.")
    return attempt


def grade_attempt(db: Session, attempt_id: int) -> tuple[Result, bool]:
    """All grading steps in one call (the LangGraph workflow runs them as separate nodes).
    Idempotent: returns (existing_result, False) if the attempt was already graded."""
    existing = get_result_for_attempt(db, attempt_id)
    if existing:
        return existing, False
    require_submitted(db, attempt_id)
    prepare_answers(db, attempt_id)
    ai_grade_text_answers(db, attempt_id)
    return ensure_result(db, attempt_id)


def manual_grade(
    db: Session, answer_id: int, awarded_points: Decimal, rationale: str, admin: User
) -> tuple[Result, bool]:
    """Admin review of a written (or AI-flagged) answer. Returns (result, became_final)."""
    answer = db.scalar(select(Answer).where(Answer.id == answer_id).with_for_update())
    if not answer:
        raise not_found("Answer")
    result = db.scalar(select(Result).where(Result.attempt_id == answer.attempt_id))
    if result is None:
        raise bad_request("This attempt has not been graded yet.")
    if answer.grading_method not in ("pending_review", "ai_assisted", "manual"):
        raise bad_request("Objective answers are graded automatically and cannot be overridden.")
    max_points = Decimal(answer.max_points)
    if awarded_points < ZERO or awarded_points > max_points:
        raise bad_request(f"Points must be between 0 and {max_points}.")
    was_final = result.result_status == "final"
    answer.awarded_points = awarded_points
    answer.is_correct = awarded_points == max_points and max_points > 0
    answer.grading_method = "manual"
    answer.grading_rationale = f"Reviewed by {admin.name}: {rationale}".strip()
    answer.grading_model = None
    answer.needs_review = False
    answer.graded_at = utcnow()
    db.commit()
    result = _upsert_result(db, answer.attempt_id)
    return result, (not was_final and result.result_status == "final")
