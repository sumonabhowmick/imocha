"""Question bank: reusable questions that are COPIED into assessments.

Copying (instead of linking) keeps version safety: editing or deleting a bank question
never changes an assessment that already uses it, or any past result.
"""
from __future__ import annotations

import random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import bad_request, not_found
from app.models import BankQuestion, Question, User
from app.schemas.assessment import QuestionCreate, validate_question_fields
from app.services import assessment_service


def bank_to_out(q: BankQuestion) -> dict:
    return {
        "id": q.id,
        "question_text": q.question_text,
        "question_type": q.question_type,
        "topic": q.topic,
        "difficulty": q.difficulty,
        "points": float(q.points),
        "options": q.options_json,
        "correct_answer": q.correct_answer,
        "grading_rubric": q.grading_rubric,
        "explanation": q.explanation,
        "created_at": q.created_at,
        "updated_at": q.updated_at,
    }


def _filtered(stmt, search, topic, question_type, difficulty):
    if search:
        stmt = stmt.where(BankQuestion.question_text.ilike(f"%{search.strip()}%"))
    if topic:
        stmt = stmt.where(func.lower(BankQuestion.topic) == topic.strip().lower())
    if question_type:
        stmt = stmt.where(BankQuestion.question_type == question_type)
    if difficulty:
        stmt = stmt.where(BankQuestion.difficulty == difficulty)
    return stmt


def list_bank(
    db: Session,
    search: str | None = None,
    topic: str | None = None,
    question_type: str | None = None,
    difficulty: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    stmt = _filtered(select(BankQuestion), search, topic, question_type, difficulty)
    total = db.scalar(_filtered(select(func.count(BankQuestion.id)), search, topic, question_type, difficulty)) or 0
    items = db.scalars(stmt.order_by(BankQuestion.updated_at.desc(), BankQuestion.id.desc()).limit(limit).offset(offset))
    topics = db.execute(
        select(BankQuestion.topic, func.count()).where(BankQuestion.topic != "").group_by(BankQuestion.topic).order_by(BankQuestion.topic)
    ).all()
    return {
        "total": total,
        "items": [bank_to_out(q) for q in items],
        "topics": [{"topic": t, "count": n} for t, n in topics],
    }


def get_bank_question(db: Session, bank_id: int) -> BankQuestion:
    q = db.get(BankQuestion, bank_id)
    if not q:
        raise not_found("Bank question")
    return q


def create_bank_question(db: Session, data: QuestionCreate, difficulty: str, admin: User) -> dict:
    q = BankQuestion(
        question_text=data.question_text,
        question_type=data.question_type,
        topic=data.topic.strip(),
        difficulty=difficulty,
        points=data.points,
        options_json=[o.model_dump() for o in data.options] if data.options else None,
        correct_answer=data.correct_answer,
        grading_rubric=data.grading_rubric,
        explanation=data.explanation,
        created_by=admin.id,
    )
    db.add(q)
    db.commit()
    return bank_to_out(q)


def update_bank_question(db: Session, bank_id: int, changes: dict) -> dict:
    q = get_bank_question(db, bank_id)
    if "options" in changes:
        changes["options_json"] = changes.pop("options")
    for field, value in changes.items():
        if field in ("question_text", "question_type", "points", "difficulty") and value is None:
            raise bad_request(f"{field} cannot be empty.")
        setattr(q, field, value)
    try:
        validate_question_fields(q.question_type, q.options_json, q.correct_answer)
    except ValueError as exc:
        db.rollback()
        raise bad_request(str(exc)) from exc
    db.commit()
    return bank_to_out(q)


def delete_bank_question(db: Session, bank_id: int) -> None:
    db.delete(get_bank_question(db, bank_id))
    db.commit()


def _copy_to_assessment(db: Session, assessment_id: int, q: BankQuestion) -> Question:
    data = QuestionCreate(
        question_text=q.question_text,
        question_type=q.question_type,
        topic=q.topic,
        points=q.points,
        options=q.options_json,
        correct_answer=q.correct_answer,
        grading_rubric=q.grading_rubric,
        explanation=q.explanation,
    )
    return assessment_service.add_question(db, assessment_id, data)


def add_to_assessment(db: Session, assessment_id: int, bank_ids: list[int]) -> dict:
    """Copy the chosen bank questions into a DRAFT assessment (in the given order)."""
    ids = list(dict.fromkeys(bank_ids))
    questions = {q.id: q for q in db.scalars(select(BankQuestion).where(BankQuestion.id.in_(ids)))}
    missing = [i for i in ids if i not in questions]
    if missing:
        raise bad_request(f"Unknown bank question ids: {missing}")
    for i in ids:
        _copy_to_assessment(db, assessment_id, questions[i])  # add_question enforces draft-only
    db.expire_all()  # reload the question list after the inserts
    return assessment_service.to_out(assessment_service.get_assessment(db, assessment_id), include_questions=True)


def random_pick(
    db: Session, assessment_id: int, count: int, topic: str | None = None, difficulty: str | None = None,
    question_type: str | None = None,
) -> dict:
    """Copy `count` random bank questions matching the filters into a DRAFT assessment,
    skipping questions whose text is already in it."""
    assessment = assessment_service.get_assessment(db, assessment_id)
    used = {q.question_text.strip() for q in assessment.questions}
    pool = [
        q for q in db.scalars(_filtered(select(BankQuestion), None, topic, question_type, difficulty))
        if q.question_text.strip() not in used
    ]
    if len(pool) < count:
        raise bad_request(f"Only {len(pool)} matching bank question(s) are available; requested {count}.")
    for q in random.SystemRandom().sample(pool, count):
        _copy_to_assessment(db, assessment_id, q)
    db.expire_all()  # reload the question list after the inserts
    return assessment_service.to_out(assessment_service.get_assessment(db, assessment_id), include_questions=True)


def save_question_to_bank(db: Session, question_id: int, difficulty: str, admin: User) -> dict:
    """Copy an existing assessment question into the bank for reuse."""
    q = db.get(Question, question_id)
    if not q:
        raise not_found("Question")
    bank = BankQuestion(
        question_text=q.question_text,
        question_type=q.question_type,
        topic=q.topic,
        difficulty=difficulty,
        points=q.points,
        options_json=q.options_json,
        correct_answer=q.correct_answer,
        grading_rubric=q.grading_rubric,
        explanation=q.explanation,
        created_by=admin.id,
    )
    db.add(bank)
    db.commit()
    return bank_to_out(bank)
