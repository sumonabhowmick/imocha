"""Quiz Builder Agent tools. They wrap the services behind the Assessments page.

Creating and editing DRAFTS is a safe write (students never see drafts, and the
existing service refuses to edit published quizzes). Publishing and archiving
require admin approval.
"""
from __future__ import annotations

from datetime import datetime

from langchain_core.runnables import RunnableConfig
from sqlalchemy.orm import Session

from app.agents.tooling import admin_from_config, agent_tool, request_approval
from app.config import get_settings
from app.schemas.assessment import AssessmentCreate, GenerateQuestionsRequest, QuestionCreate, QuestionUpdate
from app.services import assessment_service
from app.services.email_service import format_local


def _parse_time(value: str | None, field: str) -> datetime | None:
    """ISO date-time; without an offset it is read in the app timezone (Asia/Kolkata)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date-time such as 2026-10-25T10:00") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=get_settings().tz)


def _summary(assessment) -> dict:
    out = assessment_service.to_out(assessment)
    return {k: out[k] for k in ("id", "title", "status", "display_status", "question_count", "total_points", "duration_minutes", "access_start_at", "access_end_at")}


@agent_tool("create_assessment", "write")
def create_assessment(
    db: Session,
    config: RunnableConfig | None,
    title: str,
    description: str = "",
    instructions: str = "",
    category: str = "",
    difficulty: str = "intermediate",
    duration_minutes: int = 45,
    access_start_at: str | None = None,
    access_end_at: str | None = None,
    max_attempts: int = 1,
) -> dict:
    """Create a new DRAFT assessment (not visible to students). difficulty: beginner,
    intermediate or advanced. access_start_at/access_end_at: ISO date-times (app timezone
    if no offset); both are needed before publishing."""
    data = AssessmentCreate(
        title=title,
        description=description,
        instructions=instructions,
        category=category,
        difficulty=difficulty,
        duration_minutes=duration_minutes,
        access_start_at=_parse_time(access_start_at, "access_start_at"),
        access_end_at=_parse_time(access_end_at, "access_end_at"),
        max_attempts=max_attempts,
    )
    assessment = assessment_service.create_assessment(db, data, admin_from_config(db, config))
    return {"created": _summary(assessment)}


@agent_tool("add_question", "write")
def add_question(
    db: Session,
    config: RunnableConfig | None,
    assessment_id: int,
    question_text: str,
    question_type: str,
    topic: str = "",
    points: float = 1,
    options: list[dict] | None = None,
    correct_answer: str | list[str] | None = None,
    grading_rubric: str = "",
    explanation: str = "",
) -> dict:
    """Add a question to a DRAFT assessment. question_type: mcq_single (correct_answer is
    one option id), mcq_multi (correct_answer is a list of option ids), text or coding
    (use grading_rubric; no options). options: [{"id": "A", "text": "..."}, ...]."""
    data = QuestionCreate(
        question_text=question_text,
        question_type=question_type,
        topic=topic,
        points=points,
        options=options,
        correct_answer=correct_answer,
        grading_rubric=grading_rubric,
        explanation=explanation,
    )
    question = assessment_service.add_question(db, assessment_id, data)
    return {"added": assessment_service.question_to_out(question)}


@agent_tool("update_question", "write")
def update_question(
    db: Session,
    config: RunnableConfig | None,
    question_id: int,
    question_text: str | None = None,
    topic: str | None = None,
    points: float | None = None,
    options: list[dict] | None = None,
    correct_answer: str | list[str] | None = None,
    grading_rubric: str | None = None,
    explanation: str | None = None,
) -> dict:
    """Edit a question of a DRAFT assessment. Only the fields you pass are changed."""
    changes = {
        k: v
        for k, v in {
            "question_text": question_text,
            "topic": topic,
            "points": points,
            "options": options,
            "correct_answer": correct_answer,
            "grading_rubric": grading_rubric,
            "explanation": explanation,
        }.items()
        if v is not None
    }
    if not changes:
        raise ValueError("Pass at least one field to change.")
    question = assessment_service.update_question(db, question_id, QuestionUpdate(**changes))
    return {"updated": assessment_service.question_to_out(question)}


@agent_tool("generate_questions", "write")
def generate_questions(db: Session, config: RunnableConfig | None, assessment_id: int, mcq_count: int = 0, maq_count: int = 0) -> dict:
    """Use the existing AI question generator to add questions to a DRAFT assessment,
    based on its title, category, description and difficulty. mcq_count = single-answer
    questions, maq_count = multiple-answer questions (total 1-50)."""
    request = GenerateQuestionsRequest(mcq_count=mcq_count, maq_count=maq_count)
    assessment = assessment_service.generate_questions(db, assessment_id, request)
    return {"assessment": _summary(assessment)}


@agent_tool("preview_assessment", "read")
def preview_assessment(db: Session, config: RunnableConfig | None, assessment_id: int) -> dict:
    """Show an assessment's settings and all its questions, including answer keys
    (admin-only view)."""
    assessment = assessment_service.get_assessment(db, assessment_id)
    out = _summary(assessment)
    out["questions"] = [
        {k: q[k] for k in ("id", "position", "question_type", "topic", "points", "question_text", "options", "correct_answer", "grading_rubric")}
        for q in (assessment_service.question_to_out(q) for q in assessment.questions)
    ]
    return out


@agent_tool("publish_assessment", "approval")
def publish_assessment(db: Session, config: RunnableConfig | None, assessment_id: int) -> dict:
    """Publish a draft assessment so it can be assigned to students. Its questions are
    then locked. REQUIRES ADMIN APPROVAL."""
    assessment = assessment_service.get_assessment(db, assessment_id)
    summary = (
        f"Publish '{assessment.title}' ({len(assessment.questions)} questions, "
        f"window {format_local(assessment.access_start_at)} to {format_local(assessment.access_end_at)}). "
        "Its questions will be locked."
    )
    decision = request_approval("publish_assessment", summary, {"assessment_id": assessment_id, "title": assessment.title})
    if not decision.get("approved"):
        return {"status": "rejected_by_admin", "note": decision.get("note", "")}
    return {"published": _summary(assessment_service.publish_assessment(db, assessment_id))}


@agent_tool("archive_assessment", "approval")
def archive_assessment(db: Session, config: RunnableConfig | None, assessment_id: int) -> dict:
    """Archive an assessment so it can no longer be started. Results are kept.
    REQUIRES ADMIN APPROVAL."""
    assessment = assessment_service.get_assessment(db, assessment_id)
    decision = request_approval(
        "archive_assessment",
        f"Archive '{assessment.title}'. Students will no longer be able to start it; existing results are kept.",
        {"assessment_id": assessment_id, "title": assessment.title},
    )
    if not decision.get("approved"):
        return {"status": "rejected_by_admin", "note": decision.get("note", "")}
    return {"archived": _summary(assessment_service.archive_assessment(db, assessment_id))}
