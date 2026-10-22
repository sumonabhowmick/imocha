"""The four specialist agents.

Each specialist is a small ReAct-style LangGraph:

    START -> model --(tool calls?)--> tools (ToolNode) --> model ... --(no tool calls)--> END

The model decides which of ITS OWN tools to call; Python runs the tool and feeds the
result back, until the agent writes its answer. An agent cannot call another agent's
tools - each one only receives the tool set it needs (least privilege).
"""
from __future__ import annotations

from datetime import datetime

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agents import tools as T
from app.config import get_settings
from app.services.ai_service import ai_service

COMMON_RULES = """Rules for every AssessFlow AI agent:
- Do ONLY the part of the request that belongs to your role (follow the Supervisor's instruction). When your part
  is done, answer and stop: the Supervisor hands any remaining work to the right specialist.
- Use each tool only for what its description says. Never put messages, requests or instructions into data fields
  (e.g. never write a note to someone as a question).
- Use your tools to get facts. Never invent ids, names, scores, counts or statistics; quote only what tools return.
- Look ids up with a tool (e.g. list_assessments, find_candidates, list_results) instead of guessing them.
- If a request is ambiguous (e.g. two candidates match a name), ask the admin to clarify instead of guessing.
- If a tool returns {{"error": ...}}, explain the problem plainly; do not retry the same call blindly.
- If a tool returns "rejected_by_admin", do not try that action again; report that the admin declined.
- Some tools pause for the admin's approval. That is expected; never try to work around it.
- You cannot change scores or grades, delete data, see passcodes or create admin accounts.
- Do not make judgements about a person's character, honesty or employability.
- Current time: {now} ({tz}). Interpret dates without a timezone in {tz}.
- Finish with a short, clear answer for the admin describing what you found or did."""

SPECIALISTS: dict[str, dict] = {
    "analytics": {
        "title": "Analytics Agent",
        "tools": T.ANALYTICS_TOOLS,
        "prompt": "You are the Analytics Agent of AssessFlow AI. You answer questions about assessments, "
        "results, scores, topics and progress using verified data from your read-only tools. "
        "When comparing or ranking, use the numbers returned by the tools.",
    },
    "quiz_builder": {
        "title": "Quiz Builder Agent",
        "tools": T.QUIZ_BUILDER_TOOLS,
        "prompt": "You are the Quiz Builder Agent of AssessFlow AI. You create DRAFT assessments, add or edit "
        "questions (or use generate_questions), preview them, and publish or archive assessments "
        "(publishing and archiving need the admin's approval). Questions can only change while an "
        "assessment is a draft. Every multiple-choice question needs options with ids and a correct answer. "
        "An assessment needs an access window before it can be published.",
    },
    "operations": {
        "title": "Operations Agent",
        "tools": T.OPERATIONS_TOOLS,
        "prompt": "You are the Operations Agent of AssessFlow AI. You manage candidates and their assignments, "
        "send invitation emails (with the admin's approval) and check or retry email deliveries. "
        "Always work with candidate ids from find_candidates; never type an email address into a tool "
        "unless the admin gave it to you to create a new candidate. A candidate must be assigned "
        "(assign_candidates) before an invitation can be sent.",
    },
    "coaching": {
        "title": "Coaching Agent",
        "tools": T.COACHING_TOOLS,
        "prompt": "You are the Coaching Agent of AssessFlow AI. You help the admin support ONE student at a time: "
        "find the student's result, read their per-question performance and their stored AI feedback "
        "reports, and explain strengths, weak topics and next steps based only on that evidence. "
        "You can re-run a student's feedback workflow (with the admin's approval) if it failed.",
    },
}


def system_prompt(agent: str) -> str:
    settings = get_settings()
    now = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M")
    rules = COMMON_RULES.format(now=now, tz=settings.app_timezone)
    return SPECIALISTS[agent]["prompt"] + "\n\n" + rules


def build_specialist(agent: str):
    """Compile one specialist's ReAct loop as a LangGraph subgraph."""
    tools = SPECIALISTS[agent]["tools"]

    def call_model(state: MessagesState, config: RunnableConfig) -> dict:
        # One tool call at a time keeps approvals simple: at most one pending action.
        llm = ai_service.chat_model().bind_tools(tools, parallel_tool_calls=False)
        reply = llm.invoke([SystemMessage(system_prompt(agent)), *state["messages"]], config)
        reply.name = agent
        return {"messages": [reply]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    return graph.compile(name=agent)
