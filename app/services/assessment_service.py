"""Assessment and question management.

Version safety: questions can only be added, edited, deleted or reordered while an
assessment is a draft. Publishing freezes the question set, so a published assessment's
questions (and therefore every historical attempt) can never silently change meaning.
To change a published quiz, duplicate it into a new draft.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import DEFAULT_CATEGORY_SCALE, get_settings
from app.database import utcnow
from app.errors import AppError, bad_request, conflict, not_found
from app.models import Assessment, Question, User
from app.schemas.assessment import (
    AssessmentCreate,
    AssessmentUpdate,
    GenerateQuestionsRequest,
    GenerateQuestionsRequest,
    QuestionCreate,
    QuestionUpdate,
    validate_question_fields,
)
from app.services.scoring import validate_category_scale
from app.services.ai_service import AIServiceError, ai_service
from app.services.ai_service import AIServiceError, ai_service

logger = logging.getLogger(__name__)

# Fields that may still change after publication. None of them affect grading of
# existing attempts (each attempt stores its own deadline and each answer its points).
EDITABLE_AFTER_PUBLISH = {
    "title",
    "description",
    "instructions",
    "category",
    "access_start_at",
    "access_end_at",
    "duration_minutes",
    "completion_policy",
    "max_attempts",
}


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------
def display_status(assessment: Assessment, now: datetime | None = None) -> str:
    """Derive the lifecycle status shown to admins from stored status + access window."""
    now = now or utcnow()
    if assessment.status in ("draft", "archived"):
        return assessment.status
    if assessment.access_start_at and now < assessment.access_start_at:
        return "scheduled"
    if assessment.access_end_at and now >= assessment.access_end_at:
        return "completed"
    return "active"


def category_scale_for(assessment: Assessment) -> list[dict]:
    return assessment.category_scale_json or DEFAULT_CATEGORY_SCALE


def _total_points(assessment: Assessment) -> Decimal:
    return sum((q.points for q in assessment.questions), Decimal("0"))


def to_out(assessment: Assessment, include_questions: bool = False) -> dict:
    data = {
        "id": assessment.id,
        "title": assessment.title,
        "description": assessment.description,
        "instructions": assessment.instructions,
        "category": assessment.category,
        "difficulty": assessment.difficulty,
        "duration_minutes": assessment.duration_minutes,
        "access_start_at": assessment.access_start_at,
        "access_end_at": assessment.access_end_at,
        "completion_policy": assessment.completion_policy,
        "max_attempts": assessment.max_attempts,
        "status": assessment.status,
        "display_status": display_status(assessment),
        "category_scale": category_scale_for(assessment),
        "question_count": len(assessment.questions),
        "total_points": float(_total_points(assessment)),
        "published_at": assessment.published_at,
        "created_at": assessment.created_at,
        "updated_at": assessment.updated_at,
    }
    if include_questions:
        data["questions"] = [question_to_out(q) for q in assessment.questions]
    return data


def question_to_out(q: Question) -> dict:
    return {
        "id": q.id,
        "assessment_id": q.assessment_id,
        "position": q.position,
        "question_text": q.question_text,
        "question_type": q.question_type,
        "topic": q.topic,
        "points": float(q.points),
        "options": q.options_json,
        "correct_answer": q.correct_answer,
        "grading_rubric": q.grading_rubric,
        "explanation": q.explanation,
    }


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------
def get_assessment(db: Session, assessment_id: int, for_update: bool = False) -> Assessment:
    stmt = select(Assessment).where(Assessment.id == assessment_id).options(selectinload(Assessment.questions))
    if for_update:
        stmt = stmt.with_for_update(of=Assessment)
    assessment = db.scalar(stmt)
    if not assessment:
        raise not_found("Assessment")
    return assessment


def list_assessments(db: Session, status: str | None = None) -> list[Assessment]:
    stmt = select(Assessment).options(selectinload(Assessment.questions)).order_by(Assessment.created_at.desc())
    if status:
        stmt = stmt.where(Assessment.status == status)
    return list(db.scalars(stmt))


def _validate_window(start: datetime | None, end: datetime | None) -> None:
    if (start is None) != (end is None):
        raise bad_request("Set both the access-window start and end, or neither.")
    if start and end and end <= start:
        raise bad_request("The access window must end after it starts.")


def create_assessment(db: Session, data: AssessmentCreate, admin: User) -> Assessment:
    settings = get_settings()
    _validate_window(data.access_start_at, data.access_end_at)
    scale = None
    if data.category_scale:
        scale = [b.model_dump() for b in data.category_scale]
        _check_scale(scale)
    assessment = Assessment(
        title=data.title.strip(),
        description=data.description,
        instructions=data.instructions,
        category=data.category,
        difficulty=data.difficulty,
        duration_minutes=data.duration_minutes or settings.assessment_default_duration_minutes,
        access_start_at=data.access_start_at,
        access_end_at=data.access_end_at,
        completion_policy=data.completion_policy or settings.assessment_completion_policy,
        max_attempts=data.max_attempts,
        category_scale_json=scale,
        status="draft",
        created_by=admin.id,
    )
    db.add(assessment)
    db.commit()
    logger.info("Assessment %s created by admin %s", assessment.id, admin.id)
    return get_assessment(db, assessment.id)


def _check_scale(scale: list[dict]) -> None:
    try:
        validate_category_scale(scale)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc


def update_assessment(db: Session, assessment_id: int, data: AssessmentUpdate) -> Assessment:
    assessment = get_assessment(db, assessment_id, for_update=True)
    changes = data.model_dump(exclude_unset=True)
    if assessment.status == "archived":
        raise conflict("Archived assessments cannot be edited.")
    if assessment.status == "published":
        blocked = set(changes) - EDITABLE_AFTER_PUBLISH
        if blocked:
            raise conflict(
                "These fields cannot change after publication: " + ", ".join(sorted(blocked))
                + ". Duplicate the assessment to make a new version."
            )
    if "category_scale" in changes:
        scale = changes.pop("category_scale")
        if scale is not None:
            _check_scale(scale)
        assessment.category_scale_json = scale
    for field in ("title", "duration_minutes", "completion_policy", "max_attempts", "difficulty"):
        if field in changes and changes[field] is None:
            raise bad_request(f"{field} cannot be empty.")
    for field, value in changes.items():
        setattr(assessment, field, value)
    _validate_window(assessment.access_start_at, assessment.access_end_at)
    if assessment.status == "published" and not assessment.access_start_at:
        raise bad_request("A published assessment must keep an access window.")
    db.commit()
    return get_assessment(db, assessment.id)


def publish_assessment(db: Session, assessment_id: int) -> Assessment:
    assessment = get_assessment(db, assessment_id, for_update=True)
    if assessment.status != "draft":
        raise conflict(f"Only draft assessments can be published (current status: {assessment.status}).")
    if not assessment.questions:
        raise bad_request("Add at least one question before publishing.")
    if not assessment.access_start_at or not assessment.access_end_at:
        raise bad_request("Set the access window before publishing.")
    if assessment.access_end_at <= utcnow():
        raise bad_request("The access window has already ended. Choose a future window.")
    for q in assessment.questions:
        try:
            validate_question_fields(q.question_type, q.options_json, q.correct_answer)
        except ValueError as exc:
            raise bad_request(f"Question {q.position + 1}: {exc}") from exc
    assessment.status = "published"
    assessment.published_at = utcnow()
    db.commit()
    logger.info("Assessment %s published", assessment.id)
    return get_assessment(db, assessment.id)


def archive_assessment(db: Session, assessment_id: int) -> Assessment:
    assessment = get_assessment(db, assessment_id, for_update=True)
    if assessment.status == "archived":
        return assessment
    assessment.status = "archived"
    db.commit()
    return get_assessment(db, assessment.id)


def duplicate_assessment(db: Session, assessment_id: int, admin: User) -> Assessment:
    source = get_assessment(db, assessment_id)
    copy = Assessment(
        title=f"{source.title} (copy)"[:200],
        description=source.description,
        instructions=source.instructions,
        category=source.category,
        difficulty=source.difficulty,
        duration_minutes=source.duration_minutes,
        access_start_at=None,
        access_end_at=None,
        completion_policy=source.completion_policy,
        max_attempts=source.max_attempts,
        category_scale_json=source.category_scale_json,
        status="draft",
        created_by=admin.id,
    )
    for q in source.questions:
        copy.questions.append(
            Question(
                question_text=q.question_text,
                question_type=q.question_type,
                topic=q.topic,
                points=q.points,
                position=q.position,
                options_json=q.options_json,
                correct_answer=q.correct_answer,
                grading_rubric=q.grading_rubric,
                explanation=q.explanation,
            )
        )
    db.add(copy)
    db.commit()
    return get_assessment(db, copy.id)


# ---------------------------------------------------------------------------
# Questions (draft only)
# ---------------------------------------------------------------------------
def _require_draft(assessment: Assessment) -> None:
    if assessment.status != "draft":
        raise conflict(
            "Questions can only be changed while the assessment is a draft. "
            "Duplicate the assessment to create an editable version.",
            "assessment_locked",
        )


def generate_questions(db: Session, assessment_id: int, request: GenerateQuestionsRequest) -> Assessment:
    assessment = get_assessment(db, assessment_id)
    _require_draft(assessment)
    try:
        generated = ai_service.generate_questions(
            {
                "title": assessment.title,
                "category": assessment.category,
                "description": assessment.description,
                "instructions": assessment.instructions,
                "difficulty": assessment.difficulty,
            },
            request.mcq_count,
            request.maq_count,
        )
    except AIServiceError as exc:
        raise AppError(502, str(exc), "ai_generation_failed") from exc

    expected = {"mcq_single": request.mcq_count, "mcq_multi": request.maq_count}
    counts = {kind: sum(q.question_type == kind for q in generated.questions) for kind in expected}
    if counts != expected:
        raise AppError(502, "The AI did not return the requested question types and counts; nothing was saved.", "ai_invalid_questions")

    prepared = []
    for q in generated.questions:
        try:
            data = QuestionCreate.model_validate(
                {
                    "question_text": q.question_text,
                    "question_type": q.question_type,
                    "topic": q.topic,
                    "points": 1,
                    "options": [o.model_dump() for o in q.options],
                    "correct_answer": q.correct_answer,
                    "explanation": q.explanation,
                }
            )
        except Exception as exc:
            raise AppError(502, "The AI returned an invalid answer key; nothing was saved.", "ai_invalid_questions") from exc
        prepared.append(data)

    assessment = get_assessment(db, assessment_id, for_update=True)
    _require_draft(assessment)
    start_position = max((q.position for q in assessment.questions), default=-1) + 1
    for offset, data in enumerate(prepared):
        assessment.questions.append(
            Question(
                question_text=data.question_text,
                question_type=data.question_type,
                topic=data.topic,
                points=data.points,
                position=start_position + offset,
                options_json=[o.model_dump() for o in data.options] if data.options else None,
                correct_answer=data.correct_answer,
                grading_rubric="",
                explanation=data.explanation,
            )
        )
    db.commit()
    return get_assessment(db, assessment_id)


def add_question(db: Session, assessment_id: int, data: QuestionCreate) -> Question:
    assessment = get_assessment(db, assessment_id, for_update=True)
    _require_draft(assessment)
    next_position = (db.scalar(
        select(func.max(Question.position)).where(Question.assessment_id == assessment_id)
    ) or -1) + 1
    question = Question(
        assessment_id=assessment_id,
        question_text=data.question_text,
        question_type=data.question_type,
        topic=data.topic.strip(),
        points=data.points,
        position=next_position,
        options_json=[o.model_dump() for o in data.options] if data.options else None,
        correct_answer=data.correct_answer,
        grading_rubric=data.grading_rubric,
        explanation=data.explanation,
    )
    db.add(question)
    db.commit()
    return question


def _get_question_for_edit(db: Session, question_id: int) -> Question:
    question = db.get(Question, question_id)
    if not question:
        raise not_found("Question")
    assessment = get_assessment(db, question.assessment_id, for_update=True)
    _require_draft(assessment)
    return question


def update_question(db: Session, question_id: int, data: QuestionUpdate) -> Question:
    question = _get_question_for_edit(db, question_id)
    changes = data.model_dump(exclude_unset=True)
    if "options" in changes:
        changes["options_json"] = changes.pop("options")
    for field, value in changes.items():
        if field in ("question_text", "question_type", "points") and value is None:
            raise bad_request(f"{field} cannot be empty.")
        setattr(question, field, value)
    try:
        validate_question_fields(question.question_type, question.options_json, question.correct_answer)
    except ValueError as exc:
        db.rollback()
        raise bad_request(str(exc)) from exc
    db.commit()
    return question


def delete_question(db: Session, question_id: int) -> None:
    question = _get_question_for_edit(db, question_id)
    db.delete(question)
    db.commit()


def reorder_questions(db: Session, assessment_id: int, question_ids: list[int]) -> Assessment:
    assessment = get_assessment(db, assessment_id, for_update=True)
    _require_draft(assessment)
    existing = {q.id: q for q in assessment.questions}
    if sorted(question_ids) != sorted(existing):
        raise bad_request("question_ids must list every question of this assessment exactly once.")
    for position, qid in enumerate(question_ids):
        existing[qid].position = position
    db.commit()
    db.expire_all()
    return get_assessment(db, assessment_id)


def ensure_not_archived(assessment: Assessment) -> None:
    if assessment.status == "archived":
        raise AppError(409, "This assessment is archived.", "archived")
