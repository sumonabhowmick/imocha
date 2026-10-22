"""Tool sets for each specialist agent (each agent only gets the tools it needs)."""
from app.agents.tools.analytics_tools import (
    get_assessment_analytics,
    get_overview,
    get_result_detail,
    list_assessments,
    list_results,
)
from app.agents.tools.coaching_tools import get_result_feedback, retry_feedback
from app.agents.tools.operations_tools import (
    assign_candidates,
    create_candidate,
    find_candidates,
    list_assignments,
    list_email_logs,
    retry_email,
    send_invitations,
)
from app.agents.tools.quiz_tools import (
    add_question,
    archive_assessment,
    create_assessment,
    generate_questions,
    preview_assessment,
    publish_assessment,
    update_question,
)

ANALYTICS_TOOLS = [get_overview, list_assessments, get_assessment_analytics, list_results, get_result_detail]
QUIZ_BUILDER_TOOLS = [
    list_assessments,
    create_assessment,
    add_question,
    update_question,
    generate_questions,
    preview_assessment,
    publish_assessment,
    archive_assessment,
]
OPERATIONS_TOOLS = [
    list_assessments,
    find_candidates,
    create_candidate,
    assign_candidates,
    list_assignments,
    send_invitations,
    list_email_logs,
    retry_email,
]
COACHING_TOOLS = [list_results, get_result_detail, get_result_feedback, retry_feedback]
