"""Coaching Agent tools - one student at a time. They wrap the services behind the
Results and Feedback reports pages. (The Coaching Agent also reuses the read tools
list_results and get_result_detail.)"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from sqlalchemy.orm import Session

from app.agents.tooling import agent_tool, request_approval
from app.services import feedback_service, result_service


@agent_tool("get_result_feedback", "read")
def get_result_feedback(db: Session, config: RunnableConfig | None, result_id: int) -> dict:
    """The AI feedback stored for one student's result: the student-facing report and
    the admin coaching report, each with its generation status."""
    return feedback_service.get_result_feedback(db, result_id)


@agent_tool("retry_feedback", "approval")
def retry_feedback(db: Session, config: RunnableConfig | None, result_id: int, regenerate_completed: bool = False) -> dict:
    """Re-run the feedback workflow for one student's result: failed reports are
    regenerated (all reports if regenerate_completed=true), then the result email is sent
    if it has not been sent yet. Scores are never changed. REQUIRES ADMIN APPROVAL."""
    from app.workflows.assessment_workflow import retry_feedback as workflow_retry_feedback

    detail = result_service.result_detail(db, result_id)
    decision = request_approval(
        "retry_feedback",
        f"Re-run AI feedback for {detail['candidate_name']} on '{detail['assessment_title']}'"
        + (" (regenerate all reports)" if regenerate_completed else " (failed reports only)")
        + "; the result email is sent if it has not been sent yet.",
        {"result_id": result_id, "regenerate_completed": regenerate_completed},
    )
    if not decision.get("approved"):
        return {"status": "rejected_by_admin", "note": decision.get("note", "")}
    summary = workflow_retry_feedback(result_id, force=regenerate_completed)
    return {k: summary.get(k) for k in ("result_id", "result_status", "feedback", "result_email", "errors")}
