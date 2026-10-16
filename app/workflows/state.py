"""State objects passed between LangGraph nodes.

The state holds only IDs, statuses and a trace of executed steps. PostgreSQL remains
the source of truth for all data, so no LangGraph checkpointer is needed: every node
re-reads what it needs from the database and is safe to run again (idempotent).

Fields written by parallel nodes (or by many nodes) use `operator.add` reducers so
their updates are merged instead of conflicting.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class SubmissionState(TypedDict, total=False):
    # --- input ---
    attempt_id: int
    force_feedback: bool  # regenerate reports even if already completed
    # --- filled in by nodes ---
    already_graded: bool
    ai_gradable_answers: int
    ai_graded_answers: int
    result_id: int
    result_status: str  # final | provisional
    result_created: bool
    student_feedback_status: str  # completed | failed | pending
    admin_feedback_status: str
    result_email_status: str  # sent | outbox | failed | not_sent
    admin_notices: Annotated[list[str], operator.add]
    steps: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]


class GroupReportState(TypedDict, total=False):
    # --- input ---
    assessment_id: int
    # --- filled in by nodes ---
    analytics: dict  # statistics computed in Python from the database
    payload: dict  # the same statistics with candidate names replaced by refs
    ref_map: dict  # "C1" -> {candidate_id, candidate_name, result_id}
    content: dict | None  # validated LangChain output
    ai_error: str | None
    status: str  # completed | failed | insufficient_data
    feedback_id: int
    steps: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
