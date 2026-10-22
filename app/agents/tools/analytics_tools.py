"""Analytics Agent tools (read-only). They wrap the services behind the Overview,
Assessments and Results pages. Candidate emails are masked or omitted."""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from sqlalchemy.orm import Session

from app.agents.tooling import agent_tool, mask_email
from app.services import assessment_service, result_service


@agent_tool("get_overview", "read")
def get_overview(db: Session, config: RunnableConfig | None) -> dict:
    """Platform-wide counts: assessments, candidates, invitations, started and completed
    attempts, pending assessments, average score, results needing review, failed emails
    and failed feedback."""
    return result_service.overview(db)


@agent_tool("list_assessments", "read")
def list_assessments(db: Session, config: RunnableConfig | None, status: str | None = None) -> list[dict]:
    """List assessments with their id, title, lifecycle status, number of questions and
    access window. Optional status filter: draft, published or archived."""
    if status not in (None, "draft", "published", "archived"):
        raise ValueError("status must be draft, published or archived.")
    return [
        {
            "id": a.id,
            "title": a.title,
            "status": assessment_service.display_status(a),
            "category": a.category,
            "difficulty": a.difficulty,
            "question_count": len(a.questions),
            "duration_minutes": a.duration_minutes,
            "access_start_at": a.access_start_at,
            "access_end_at": a.access_end_at,
        }
        for a in assessment_service.list_assessments(db, status)
    ]


@agent_tool("get_assessment_analytics", "read")
def get_assessment_analytics(db: Session, config: RunnableConfig | None, assessment_id: int) -> dict:
    """Verified statistics for one assessment: assigned/started/completed counts, average,
    highest and lowest score, score distribution, category counts, per-topic and
    per-question performance, and each candidate's percentage and weak topics."""
    data = result_service.assessment_analytics(db, assessment_id)
    data.pop("category_scale", None)
    return data


@agent_tool("list_results", "read")
def list_results(
    db: Session,
    config: RunnableConfig | None,
    assessment_id: int | None = None,
    candidate_name: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List graded results (newest first) with result id, candidate name, assessment,
    score, percentage, category, status (final/provisional) and feedback/email status.
    Filter by assessment_id and/or part of a candidate's name."""
    rows = result_service.list_results(db, assessment_id=assessment_id, limit=max(1, min(limit, 200)))["items"]
    if candidate_name:
        rows = [r for r in rows if candidate_name.strip().lower() in r["candidate_name"].lower()]
    keep = (
        "result_id", "assessment_id", "assessment_title", "candidate_id", "candidate_name", "raw_score",
        "max_score", "percentage", "category", "result_status", "submitted_at", "auto_submitted",
        "student_feedback_status", "admin_feedback_status", "result_email_status",
    )
    return [{k: r.get(k) for k in keep} for r in rows]


@agent_tool("get_result_detail", "read")
def get_result_detail(db: Session, config: RunnableConfig | None, result_id: int) -> dict:
    """One student's result: score, percentage, category and every question with topic,
    points awarded, outcome (correct/incorrect/partial/unanswered/pending_review) and
    grading method."""
    data = result_service.result_detail(db, result_id)
    data["candidate_email"] = mask_email(data.get("candidate_email"))
    data.pop("category_scale", None)
    data["questions"] = [
        {k: q.get(k) for k in ("number", "topic", "question_type", "question_text", "max_points", "awarded_points", "outcome", "grading_method", "needs_review")}
        for q in data["questions"]
    ]
    return data
