"""AI feedback generation (student, admin and group reports).

* Evidence is built from verified database rows only.
* No names or email addresses are sent to the model (candidates are "the candidate"
  or pseudonymous refs like "C1"); names are added back by the application.
* Feedback is stored separately from results: a failed AI call marks the feedback as
  failed and never touches the verified score.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import utcnow
from app.errors import bad_request, not_found
from app.models import Feedback, Result
from app.services import result_service
from app.services.ai_service import AIServiceError, ai_service

logger = logging.getLogger(__name__)

MAX_ANSWER_CHARS = 2000


def _truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + " [truncated]"


def build_evidence(db: Session, result_id: int, audience: str) -> dict:
    """Minimal, verified evidence for one result. `audience` is 'student' or 'admin'.

    The student version omits correct answers and the student's raw answers; the admin
    version includes them so coaching notes can cite specific mistakes."""
    detail = result_service.result_detail(db, result_id)
    topics: dict[str, dict] = {}
    questions = []
    for q in detail["questions"]:
        topic = q["topic"] or "General"
        t = topics.setdefault(topic, {"topic": topic, "points_awarded": 0.0, "points_possible": 0.0, "questions": 0})
        t["points_awarded"] += q["awarded_points"] or 0.0
        t["points_possible"] += q["max_points"] or 0.0
        t["questions"] += 1
        item = {
            "question_number": q["number"],
            "topic": topic,
            "question_type": q["question_type"],
            "question_text": _truncate(q["question_text"], 600),
            "points_possible": q["max_points"],
            "points_awarded": q["awarded_points"],
            "outcome": q["outcome"],
            "grading_method": q["grading_method"],
        }
        if audience == "admin":
            item["student_answer"] = _truncate(q["student_answer"], MAX_ANSWER_CHARS)
            item["correct_answer"] = _truncate(q["correct_answer"], 600)
            if q["grading_rationale"]:
                item["grading_rationale"] = _truncate(q["grading_rationale"], 800)
        questions.append(item)
    return {
        "assessment": {
            "title": detail["assessment_title"],
            "question_count": len(questions),
        },
        "official_result": {
            "raw_score": detail["raw_score"],
            "max_score": detail["max_score"],
            "percentage": detail["percentage"],
            "performance_category": detail["category"],
            "category_note": "Project-defined label for this assessment only.",
            "result_status": detail["result_status"],
            "grading_method": detail["grading_method"],
            "correct": detail["correct_count"],
            "incorrect": detail["incorrect_count"],
            "unanswered": detail["unanswered_count"],
            "auto_submitted_at_deadline": detail["auto_submitted"],
        },
        "topic_summary": list(topics.values()),
        "questions": questions,
    }


def _get_or_create(db: Session, result_id: int, feedback_type: str) -> Feedback:
    fb = db.scalar(select(Feedback).where(Feedback.result_id == result_id, Feedback.feedback_type == feedback_type))
    if fb:
        return fb
    fb = Feedback(result_id=result_id, feedback_type=feedback_type, generation_status="pending")
    db.add(fb)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        fb = db.scalar(
            select(Feedback).where(Feedback.result_id == result_id, Feedback.feedback_type == feedback_type)
        )
    return fb


def generate_feedback(db: Session, result_id: int, feedback_type: str, force: bool = False) -> str:
    """Generate one report ('student' or 'admin') for a final result with the matching
    LangChain chain. A completed report is kept unless `force` is True. Returns the status.
    A failure is recorded on the feedback row only; the verified result is never touched."""
    result = db.get(Result, result_id)
    if result is None:
        raise not_found("Result")
    if result.result_status != "final":
        raise bad_request("Feedback is generated after every answer has been graded (result is provisional).")
    fb = _get_or_create(db, result_id, feedback_type)
    if fb.generation_status == "completed" and not force:
        return fb.generation_status
    try:
        evidence = build_evidence(db, result_id, feedback_type)
        content = (
            ai_service.student_feedback(evidence) if feedback_type == "student" else ai_service.admin_feedback(evidence)
        )
        fb.content = content.model_dump()
        fb.generation_status = "completed"
        fb.model_name = ai_service.model_name
        fb.error_message = None
        fb.generated_at = utcnow()
    except AIServiceError as exc:
        fb.generation_status = "failed"
        fb.error_message = str(exc)[:500]
        logger.warning("%s feedback for result %s failed: %s", feedback_type, result_id, exc)
    db.commit()
    return fb.generation_status


def generate_result_feedback(db: Session, result_id: int, force: bool = False) -> dict[str, str]:
    """Generate both reports sequentially (the LangGraph workflow runs them in parallel)."""
    return {t: generate_feedback(db, result_id, t, force=force) for t in ("student", "admin")}


def get_result_feedback(db: Session, result_id: int) -> dict:
    if db.get(Result, result_id) is None:
        raise not_found("Result")
    out = {"student": None, "admin": None}
    for fb in db.scalars(select(Feedback).where(Feedback.result_id == result_id)):
        out[fb.feedback_type] = feedback_to_out(fb)
    return out


def feedback_to_out(fb: Feedback | None) -> dict | None:
    if fb is None:
        return None
    return {
        "id": fb.id,
        "feedback_type": fb.feedback_type,
        "generation_status": fb.generation_status,
        "content": fb.content,
        "model_name": fb.model_name,
        "error_message": fb.error_message,
        "generated_at": fb.generated_at.isoformat() if fb.generated_at else None,
    }


# ---------------------------------------------------------------------------
# Group report
# ---------------------------------------------------------------------------
def group_statistics_for_ai(analytics: dict) -> tuple[dict, dict[str, dict]]:
    """Strip names from the computed analytics. Returns (payload, ref -> candidate map)."""
    ref_map = {}
    candidates = []
    for i, c in enumerate(analytics["candidates"], start=1):
        ref = f"C{i}"
        ref_map[ref] = {"candidate_id": c["candidate_id"], "candidate_name": c["candidate_name"], "result_id": c["result_id"]}
        candidates.append(
            {
                "candidate_ref": ref,
                "percentage": c["percentage"],
                "category": c["category"],
                "topics_below_50_percent": c["topics_below_50_percent"],
            }
        )
    payload = {
        "assessment_title": analytics["assessment_title"],
        "counts": {
            k: analytics[k]
            for k in (
                "total_assigned",
                "invitations_sent",
                "started",
                "completed",
                "pending",
                "expired_or_failed_invitations",
                "valid_results",
            )
        },
        "scores": {
            "average_percent": analytics["average_percent"],
            "highest_percent": analytics["highest_percent"],
            "lowest_percent": analytics["lowest_percent"],
            "distribution": analytics["score_distribution"],
            "category_counts": analytics["category_counts"],
        },
        "topics": analytics["topics"],
        "questions": [
            {k: q[k] for k in ("number", "topic", "responses", "full_credit_rate_percent", "unanswered")}
            for q in analytics["questions"]
        ],
        "candidates": candidates,
    }
    return payload, ref_map


def map_group_refs(content: dict, ref_map: dict[str, dict]) -> dict:
    """Map pseudonymous refs ("C1") back to real candidates inside the application.
    A ref the model invented is kept but flagged as unverified."""
    for item in content.get("candidates_needing_support", []):
        known = ref_map.get(item["candidate_ref"])
        item["candidate_name"] = known["candidate_name"] if known else None
        item["result_id"] = known["result_id"] if known else None
        item["unverified_ref"] = known is None
    return content


def save_group_feedback(
    db: Session, assessment_id: int, content: dict | None, error: str | None, statistics: dict | None = None
) -> Feedback:
    """Store (or replace) the single group report for an assessment."""
    fb = db.scalar(select(Feedback).where(Feedback.assessment_id == assessment_id, Feedback.feedback_type == "group"))
    if fb is None:
        fb = Feedback(assessment_id=assessment_id, feedback_type="group", generation_status="pending")
        db.add(fb)
    if content is not None:
        content["statistics_snapshot"] = {
            "valid_results": statistics.get("valid_results") if statistics else None,
            "average_percent": statistics.get("average_percent") if statistics else None,
            "computed_at": utcnow().isoformat(),
        }
        fb.content = content
        fb.generation_status = "completed"
        fb.model_name = ai_service.model_name
        fb.error_message = None
        fb.generated_at = utcnow()
    else:
        fb.generation_status = "failed"
        fb.error_message = (error or "Unknown error")[:500]
    db.commit()
    return fb


def generate_group_feedback(db: Session, assessment_id: int) -> Feedback:
    """Run the LangGraph group-report workflow and return the stored group report.
    Raises a 400 AppError when there are no final results to summarize."""
    from app.models import Assessment
    from app.workflows.assessment_workflow import ensure_group_report_ok, run_group_report

    if db.get(Assessment, assessment_id) is None:
        raise not_found("Assessment")
    state = run_group_report(assessment_id)
    ensure_group_report_ok(state)
    fb = db.scalar(
        select(Feedback)
        .where(Feedback.assessment_id == assessment_id, Feedback.feedback_type == "group")
        .execution_options(populate_existing=True)
    )
    if fb is None:
        raise not_found("Group report")
    return fb


def get_group_feedback(db: Session, assessment_id: int) -> dict | None:
    fb = db.scalar(select(Feedback).where(Feedback.assessment_id == assessment_id, Feedback.feedback_type == "group"))
    return feedback_to_out(fb)
