"""LangGraph workflow that processes a submitted attempt.

    START -> load_attempt -+-> grade_objective -+-> ai_grade_text --+
                           |                    +-------------------+-> compute_result
                           +-- (already graded) -------------------------^      |
                                                                    notify_admin_result
                                                         provisional |          | final
                                                                END <+    +-----+-----+
                                                                   student_feedback  admin_feedback   (parallel)
                                                                          +-----+-----+
                                                                          review_feedback
                                                           student report ok |    | failed
                                                               send_result_email  notify_feedback_failed
                                                                          +---> END <---+

Deterministic Python does all grading, scoring and categorizing. The LLM is used only
inside ai_grade_text (optional rubric scoring, bounded in Python) and the two feedback
nodes (LangChain chains). Each node is idempotent, so the same graph is used for first
processing, admin retries and post-review processing.
"""
from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.models import Result
from app.services import email_service, feedback_service, grading_service
from app.workflows.nodes import go_to, graph_node
from app.workflows.state import SubmissionState


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
@graph_node("load_attempt")
def load_attempt(state: SubmissionState, db: Session) -> dict:
    existing = grading_service.get_result_for_attempt(db, state["attempt_id"])
    if existing:
        return {"already_graded": True}
    grading_service.require_submitted(db, state["attempt_id"])  # raises if still in progress
    return {"already_graded": False}


@graph_node("grade_objective")
def grade_objective(state: SubmissionState, db: Session) -> dict:
    """Deterministic multiple-choice grading; written answers become pending_review."""
    grading_service.prepare_answers(db, state["attempt_id"])
    gradable = grading_service.count_ai_gradable(db, state["attempt_id"]) if grading_service.ai_text_grading_enabled() else 0
    return {"ai_gradable_answers": gradable}


@graph_node("ai_grade_text")
def ai_grade_text(state: SubmissionState, db: Session) -> dict:
    """LangChain rubric chain suggests scores; Python clamps them and flags doubts for review."""
    return {"ai_graded_answers": grading_service.ai_grade_text_answers(db, state["attempt_id"])}


@graph_node("compute_result")
def compute_result(state: SubmissionState, db: Session) -> dict:
    result, created = grading_service.ensure_result(db, state["attempt_id"])
    return {"result_id": result.id, "result_status": result.result_status, "result_created": created}


@graph_node("notify_admin_result")
def notify_admin_result(state: SubmissionState, db: Session) -> dict:
    log = email_service.notify_admin(db, db.get(Result, state["result_id"]), "admin_result_ready")
    return {"admin_notices": [f"result_ready:{log.status}"] if log else []}


def _feedback_node(feedback_type: str):
    @graph_node(f"{feedback_type}_feedback")
    def node(state: SubmissionState, db: Session) -> dict:
        status = feedback_service.generate_feedback(
            db, state["result_id"], feedback_type, force=state.get("force_feedback", False)
        )
        return {f"{feedback_type}_feedback_status": status}

    return node


student_feedback = _feedback_node("student")
admin_feedback = _feedback_node("admin")


@graph_node("review_feedback")
def review_feedback(state: SubmissionState, db: Session) -> dict:
    """Join point after both parallel feedback nodes (routing happens on its edges)."""
    return {}


@graph_node("send_result_email")
def send_result_email(state: SubmissionState, db: Session) -> dict:
    log = email_service.send_result_email(db, state["result_id"])  # at most one per result
    return {"result_email_status": log.status if log else "not_sent_or_already_sent"}


@graph_node("notify_feedback_failed")
def notify_feedback_failed(state: SubmissionState, db: Session) -> dict:
    log = email_service.notify_admin(db, db.get(Result, state["result_id"]), "admin_feedback_failed")
    return {"admin_notices": [f"feedback_failed:{log.status}"] if log else [], "result_email_status": "not_sent"}


# ---------------------------------------------------------------------------
# Routing functions (pure; they only read the state)
# ---------------------------------------------------------------------------
def route_after_load(state: SubmissionState) -> str:
    if state.get("errors"):
        return END
    return "compute_result" if state.get("already_graded") else "grade_objective"


def route_text_grading(state: SubmissionState) -> str:
    if state.get("errors"):
        return END
    return "ai_grade_text" if state.get("ai_gradable_answers", 0) > 0 else "compute_result"


def route_by_result_status(state: SubmissionState) -> list[str] | str:
    if state.get("errors") or state.get("result_status") != "final":
        return END  # provisional: wait for the admin's manual review
    return ["student_feedback", "admin_feedback"]  # fan out: run both in parallel


def route_after_feedback(state: SubmissionState) -> str:
    if state.get("errors"):
        return END
    return "send_result_email" if state.get("student_feedback_status") == "completed" else "notify_feedback_failed"


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
@lru_cache
def build_submission_graph():
    graph = StateGraph(SubmissionState)
    graph.add_node("load_attempt", load_attempt)
    graph.add_node("grade_objective", grade_objective)
    graph.add_node("ai_grade_text", ai_grade_text)
    graph.add_node("compute_result", compute_result)
    graph.add_node("notify_admin_result", notify_admin_result)
    graph.add_node("student_feedback", student_feedback)
    graph.add_node("admin_feedback", admin_feedback)
    graph.add_node("review_feedback", review_feedback)
    graph.add_node("send_result_email", send_result_email)
    graph.add_node("notify_feedback_failed", notify_feedback_failed)

    graph.add_edge(START, "load_attempt")
    graph.add_conditional_edges("load_attempt", route_after_load, ["grade_objective", "compute_result", END])
    graph.add_conditional_edges("grade_objective", route_text_grading, ["ai_grade_text", "compute_result", END])
    graph.add_conditional_edges("ai_grade_text", go_to("compute_result"), ["compute_result", END])
    graph.add_conditional_edges("compute_result", go_to("notify_admin_result"), ["notify_admin_result", END])
    graph.add_conditional_edges(
        "notify_admin_result", route_by_result_status, ["student_feedback", "admin_feedback", END]
    )
    graph.add_edge(["student_feedback", "admin_feedback"], "review_feedback")  # waits for both
    graph.add_conditional_edges(
        "review_feedback", route_after_feedback, ["send_result_email", "notify_feedback_failed", END]
    )
    graph.add_edge("send_result_email", END)
    graph.add_edge("notify_feedback_failed", END)
    return graph.compile()
