"""Multi-agent assistant: a Supervisor that delegates to four specialist agents.

    START -> supervisor --(route)--> analytics | quiz_builder | operations | coaching
                 ^                          |
                 +------ (back to supervisor after each specialist) ---+
             supervisor --(FINISH)--> END

* The Supervisor has no tools. It reads the conversation, picks the next specialist
  (with a short instruction) or finishes with a summary for the admin. Its decision is
  a LangChain chain validated by Pydantic, like every other AI call in the project.
* Specialists are LangGraph subgraphs (app/agents/specialists.py) with their own tools.
* Risky tools call interrupt(); the whole graph pauses and its state is saved by the
  checkpointer. The admin approves/rejects via the API, and the graph resumes where it
  stopped with Command(resume=...).

The checkpointer is in memory (one server process): a paused approval is lost if the
server restarts, and the admin simply asks again.
"""
from __future__ import annotations

import json
import logging
import uuid
from functools import lru_cache
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agents.specialists import SPECIALISTS, build_specialist
from app.agents.tooling import TOOL_RISK
from app.errors import AppError
from app.services.ai_service import AIServiceError, ai_service

logger = logging.getLogger(__name__)

AgentName = Literal["analytics", "quiz_builder", "operations", "coaching"]
MAX_DELEGATIONS = 6  # supervisor hand-offs per admin message (stops endless loops)
RECURSION_LIMIT = 80


class RouteDecision(BaseModel):
    next: Literal["analytics", "quiz_builder", "operations", "coaching", "FINISH"]
    instruction: str = Field("", max_length=1000)  # what the chosen specialist should do
    answer: str = Field("", max_length=3000)  # final reply to the admin when next == FINISH


class AssistantState(MessagesState, total=False):
    next: str
    delegations: int


SUPERVISOR_RULES = """You are the Supervisor of the AssessFlow AI admin assistant. You do not use tools yourself;
you delegate to specialist agents and decide when the admin's request is complete.

Specialists:
- analytics: questions about assessments, results, scores, topics, statistics and overall progress (read-only).
- quiz_builder: create draft assessments, add/edit/generate questions, preview, publish or archive assessments.
- operations: find/create candidates, assign assessments, send invitation emails, check or retry email deliveries.
- coaching: one specific student's result, their AI feedback reports, what they should improve; re-run their feedback.

How to decide:
- Read the latest admin request and everything the specialists have done since.
- Choose the ONE specialist that should act next and give it a short, specific instruction including any ids already found.
- A request may need several specialists in turn (e.g. analytics finds weak students, then operations invites them).
- Choose FINISH when the request is complete, when a specialist asked the admin a question, when an action was
  rejected by the admin, or when the request is outside these specialists' abilities. With FINISH, write `answer`:
  a short summary for the admin of what was found or done, using only facts from the conversation.
- Never send a specialist to repeat work that is already done.

Respond with ONE JSON object only that matches this schema:
{format_instructions}"""


# ---------------------------------------------------------------------------
# Supervisor node
# ---------------------------------------------------------------------------
def _render(messages: list[BaseMessage], limit: int = 40) -> str:
    """Readable transcript for the supervisor (tool results shortened)."""
    lines = []
    for m in messages[-limit:]:
        if isinstance(m, HumanMessage):
            who = "SUPERVISOR -> specialist" if m.name == "supervisor" else "ADMIN"
            lines.append(f"{who}: {m.content}")
        elif isinstance(m, AIMessage):
            for call in m.tool_calls or []:
                lines.append(f"{m.name or 'agent'} called {call['name']}({json.dumps(call['args'], default=str)[:300]})")
            if m.content:
                lines.append(f"{m.name or 'agent'}: {m.content}")
        elif isinstance(m, ToolMessage):
            lines.append(f"  result of {m.name}: {str(m.content)[:600]}")
    return "\n".join(lines)


@lru_cache
def _route_parser() -> PydanticOutputParser:
    return PydanticOutputParser(pydantic_object=RouteDecision)


def supervisor(state: AssistantState, config) -> dict:
    delegations = state.get("delegations", 0)
    if delegations >= MAX_DELEGATIONS:
        return {"next": "FINISH", "messages": [AIMessage(content="I stopped after several steps to avoid looping. Here is what was done so far; please ask a follow-up if something is missing.", name="supervisor")]}
    parser = _route_parser()
    prompt = ChatPromptTemplate.from_messages(
        [("system", SUPERVISOR_RULES), ("human", "Conversation so far:\n{transcript}")]
    ).partial(format_instructions=parser.get_format_instructions())
    llm = ai_service.chat_model().bind(response_format={"type": "json_object"})
    chain = (prompt | llm | parser).with_retry(stop_after_attempt=2)
    decision: RouteDecision = chain.invoke({"transcript": _render(state["messages"])}, config)

    if decision.next == "FINISH":
        reply = decision.answer.strip()
        return {"next": "FINISH", "messages": [AIMessage(content=reply, name="supervisor")] if reply else []}
    instruction = decision.instruction.strip() or "Handle the admin's latest request."
    return {
        "next": decision.next,
        "delegations": delegations + 1,
        "messages": [HumanMessage(content=f"[Supervisor to {SPECIALISTS[decision.next]['title']}] {instruction}", name="supervisor")],
    }


def _route(state: AssistantState) -> str:
    return END if state.get("next") in (None, "FINISH") else state["next"]


def _new_turn(state: AssistantState) -> dict:
    """Reset the per-request hand-off counter at the start of every admin message."""
    return {"delegations": 0, "next": ""}


@lru_cache
def build_assistant_graph():
    graph = StateGraph(AssistantState)
    graph.add_node("new_turn", _new_turn)
    graph.add_node("supervisor", supervisor)
    for name in SPECIALISTS:
        graph.add_node(name, build_specialist(name))  # specialist subgraph as a node
        graph.add_edge(name, "supervisor")
    graph.add_edge(START, "new_turn")
    graph.add_edge("new_turn", "supervisor")
    graph.add_conditional_edges("supervisor", _route, [*SPECIALISTS, END])
    return graph.compile(checkpointer=InMemorySaver())


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def _config(thread_id: str, admin_id: int) -> dict:
    return {"configurable": {"thread_id": thread_id, "admin_id": admin_id}, "recursion_limit": RECURSION_LIMIT}


def _check_owner(thread_id: str, admin_id: int) -> None:
    # Thread ids embed the owning admin; another admin's conversation looks "not found".
    if not thread_id.startswith(f"a{admin_id}-"):
        raise AppError(404, "Conversation not found.", "not_found")


def _trace(messages: list[BaseMessage]) -> list[dict]:
    steps = []
    for m in messages:
        if isinstance(m, HumanMessage) and m.name == "supervisor":
            steps.append({"type": "delegate", "agent": "supervisor", "text": m.content})
        elif isinstance(m, AIMessage):
            for call in m.tool_calls or []:
                steps.append({"type": "tool_call", "agent": m.name, "tool": call["name"], "risk": TOOL_RISK.get(call["name"]), "args": call["args"]})
            if m.content:
                steps.append({"type": "message", "agent": m.name, "text": m.content})
        elif isinstance(m, ToolMessage):
            steps.append({"type": "tool_result", "tool": m.name, "text": str(m.content)[:800]})
    return steps


def _outcome(graph, config: dict, start_index: int) -> dict:
    state = graph.get_state(config)
    messages = state.values.get("messages", [])
    new = messages[start_index:]
    interrupts = list(state.interrupts)
    if not interrupts:  # interrupts raised inside a specialist subgraph live on its task
        for task in state.tasks:
            interrupts.extend(task.interrupts)
    pending = interrupts[0].value if interrupts else None
    answer = None
    if pending is None:
        for m in reversed(new):
            if isinstance(m, AIMessage) and m.content and not m.tool_calls:
                answer = m.content
                break
    return {
        "thread_id": config["configurable"]["thread_id"],
        "status": "needs_approval" if pending else "completed",
        "answer": answer,
        "pending_approval": pending,
        "trace": _trace(new),
    }


def _invoke(graph, payload, config: dict) -> None:
    try:
        graph.invoke(payload, config)
    except AIServiceError as exc:
        raise AppError(503, str(exc), "ai_unavailable") from exc
    except AppError:
        raise
    except Exception as exc:
        logger.exception("Assistant run failed")
        raise AppError(502, f"The assistant could not finish this request ({type(exc).__name__}). Please try again.", "assistant_failed") from exc


def chat(admin_id: int, message: str, thread_id: str | None = None) -> dict:
    graph = build_assistant_graph()
    if thread_id:
        _check_owner(thread_id, admin_id)
        config = _config(thread_id, admin_id)
        state = graph.get_state(config)
        if not state.values:
            raise AppError(404, "Conversation not found (it may have expired after a server restart).", "not_found")
        if state.interrupts or any(t.interrupts for t in state.tasks):
            raise AppError(409, "Approve or reject the pending action before sending a new message.", "approval_pending")
        start = len(state.values.get("messages", []))
    else:
        thread_id = f"a{admin_id}-{uuid.uuid4().hex}"
        config = _config(thread_id, admin_id)
        start = 0
    _invoke(graph, {"messages": [HumanMessage(content=message)]}, config)
    return _outcome(graph, config, start)


def decide(admin_id: int, thread_id: str, approved: bool, note: str = "") -> dict:
    graph = build_assistant_graph()
    _check_owner(thread_id, admin_id)
    config = _config(thread_id, admin_id)
    state = graph.get_state(config)
    if not (state.interrupts or any(t.interrupts for t in state.tasks)):
        raise AppError(409, "There is no pending action in this conversation.", "no_pending_action")
    start = len(state.values.get("messages", []))
    logger.info("Admin %s %s pending agent action in %s", admin_id, "approved" if approved else "rejected", thread_id)
    _invoke(graph, Command(resume={"approved": approved, "note": note}), config)
    return _outcome(graph, config, start)


def describe_agents() -> list[dict]:
    """Agent and tool catalogue for the dashboard (names, risk levels)."""
    return [
        {
            "agent": name,
            "title": spec["title"],
            "tools": [{"name": t.name, "risk": TOOL_RISK[t.name], "description": t.description.split("\n")[0]} for t in spec["tools"]],
        }
        for name, spec in SPECIALISTS.items()
    ]
