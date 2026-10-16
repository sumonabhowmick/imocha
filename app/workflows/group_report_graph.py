"""LangGraph workflow that builds the group-level admin report for one assessment.

    START -> compute_statistics --(no final results)--> END  (status: insufficient_data)
                    |
              pseudonymize          names -> "C1", "C2", ... (no personal data to the model)
                    |
            generate_summary        LangChain group chain (summarizes, never calculates)
                    |
        (ok) map_candidate_refs     "C1" -> real candidate, unknown refs flagged
                    |       \\ (AI failed)
               save_report <-+      one group report row per assessment
                    |
                   END

All statistics are computed in Python from PostgreSQL before the model is called.
"""
from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.services import feedback_service, result_service
from app.services.ai_service import AIServiceError, ai_service
from app.workflows.nodes import go_to, graph_node
from app.workflows.state import GroupReportState


@graph_node("compute_statistics")
def compute_statistics(state: GroupReportState, db: Session) -> dict:
    analytics = result_service.assessment_analytics(db, state["assessment_id"])
    update: dict = {"analytics": analytics}
    if analytics["valid_results"] == 0:
        update["status"] = "insufficient_data"
    return update


@graph_node("pseudonymize")
def pseudonymize(state: GroupReportState, db: Session) -> dict:
    payload, ref_map = feedback_service.group_statistics_for_ai(state["analytics"])
    return {"payload": payload, "ref_map": ref_map}


@graph_node("generate_summary")
def generate_summary(state: GroupReportState, db: Session) -> dict:
    try:
        content = ai_service.group_feedback(state["payload"]).model_dump()
        return {"content": content, "ai_error": None}
    except AIServiceError as exc:
        return {"content": None, "ai_error": str(exc)}


@graph_node("map_candidate_refs")
def map_candidate_refs(state: GroupReportState, db: Session) -> dict:
    return {"content": feedback_service.map_group_refs(state["content"], state["ref_map"])}


@graph_node("save_report")
def save_report(state: GroupReportState, db: Session) -> dict:
    fb = feedback_service.save_group_feedback(
        db, state["assessment_id"], state.get("content"), state.get("ai_error"), state.get("analytics")
    )
    return {"feedback_id": fb.id, "status": fb.generation_status}


def route_after_statistics(state: GroupReportState) -> str:
    if state.get("errors") or state.get("status") == "insufficient_data":
        return END
    return "pseudonymize"


def route_after_summary(state: GroupReportState) -> str:
    if state.get("errors"):
        return END
    return "map_candidate_refs" if state.get("content") else "save_report"


@lru_cache
def build_group_report_graph():
    graph = StateGraph(GroupReportState)
    graph.add_node("compute_statistics", compute_statistics)
    graph.add_node("pseudonymize", pseudonymize)
    graph.add_node("generate_summary", generate_summary)
    graph.add_node("map_candidate_refs", map_candidate_refs)
    graph.add_node("save_report", save_report)

    graph.add_edge(START, "compute_statistics")
    graph.add_conditional_edges("compute_statistics", route_after_statistics, ["pseudonymize", END])
    graph.add_conditional_edges("pseudonymize", go_to("generate_summary"), ["generate_summary", END])
    graph.add_conditional_edges("generate_summary", route_after_summary, ["map_candidate_refs", "save_report", END])
    graph.add_conditional_edges("map_candidate_refs", go_to("save_report"), ["save_report", END])
    graph.add_edge("save_report", END)
    return graph.compile()
