"""Multi-agent assistant tests (Supervisor + 4 specialists + tools + approvals).

A scripted LangChain chat model plays every agent: it recognises which agent is calling
from the system prompt and replies with the next scripted step (a routing decision, a
tool call or a final answer). Tools, services, the database and email run for real.
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select

from app.agents import supervisor_graph
from app.agents.tooling import TOOL_RISK
from app.models import Assessment
from app.services.ai_service import ai_service
from conftest import classify_prompt
from tests.helpers import (
    ADMIN_EMAIL,
    admin_headers,
    assign,
    create_assessment,
    create_candidate,
    full_flow,
    make_admin,
    setup_admin,
)

AGENT_MARKERS = {
    "supervisor": "You are the Supervisor",
    "analytics": "You are the Analytics Agent",
    "quiz_builder": "You are the Quiz Builder Agent",
    "operations": "You are the Operations Agent",
    "coaching": "You are the Coaching Agent",
}


class AgentScript:
    """Queues of scripted replies per agent, plus a log of what each agent saw."""

    def __init__(self, fallback=None) -> None:
        self.fallback = fallback  # conftest's FakeAI, for the non-agent chains (feedback etc.)
        self.queues: dict[str, deque] = defaultdict(deque)
        self.seen: dict[str, list[str]] = defaultdict(list)

    def route(self, to: str, instruction: str = "do it") -> "AgentScript":
        self.queues["supervisor"].append(json.dumps({"next": to, "instruction": instruction, "answer": ""}))
        return self

    def finish(self, answer: str) -> "AgentScript":
        self.queues["supervisor"].append(json.dumps({"next": "FINISH", "instruction": "", "answer": answer}))
        return self

    def call(self, agent: str, tool: str, **args) -> "AgentScript":
        self.queues[agent].append(AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": f"call_{tool}_{len(self.queues[agent])}"}]))
        return self

    def say(self, agent: str, text: str) -> "AgentScript":
        self.queues[agent].append(AIMessage(content=text))
        return self

    def reply(self, messages) -> AIMessage:
        system = str(messages[0].content)
        agent = next((a for a, marker in AGENT_MARKERS.items() if marker in system), "unknown")
        if agent == "unknown" and self.fallback is not None:
            kind = classify_prompt(system)
            return AIMessage(content=self.fallback.handler(kind, system, str(messages[-1].content)))
        self.seen[agent].append(str(messages[-1].content))
        if not self.queues[agent]:
            raise AssertionError(f"No scripted reply left for {agent}")
        item = self.queues[agent].popleft()
        return AIMessage(content=item) if isinstance(item, str) else item.model_copy()


class ScriptedAgentModel(BaseChatModel):
    script: Any

    @property
    def _llm_type(self) -> str:
        return "scripted-agents"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.script.reply(messages))])


@pytest.fixture
def script(fake_ai):
    s = AgentScript(fallback=fake_ai)
    ai_service.use_llm_factory(lambda: ScriptedAgentModel(script=s))
    yield s
    ai_service.use_llm_factory(None)


def chat(client, headers, message, thread_id=None):
    return client.post("/api/admin/agents/chat", json={"message": message, "thread_id": thread_id}, headers=headers)


def decide(client, headers, thread_id, approved, note=""):
    return client.post(f"/api/admin/agents/{thread_id}/decision", json={"approved": approved, "note": note}, headers=headers)


def tools_called(body) -> list[str]:
    return [s["tool"] for s in body["trace"] if s["type"] == "tool_call"]


def invitation_emails(mailbox, address):
    return [m for m in mailbox.to(address) if "invitation" in m["Subject"].lower()]


# ---------------------------------------------------------------------------
# Least privilege
# ---------------------------------------------------------------------------
def test_agent_catalogue_and_least_privilege(client):
    headers = setup_admin(client)
    catalogue = client.get("/api/admin/agents", headers=headers).json()
    tools = {a["agent"]: {t["name"]: t["risk"] for t in a["tools"]} for a in catalogue}

    assert set(tools) == {"analytics", "quiz_builder", "operations", "coaching"}
    assert set(tools["analytics"].values()) == {"read"}  # analytics cannot change anything
    assert "send_invitations" in tools["operations"] and "send_invitations" not in tools["quiz_builder"]
    approval = {n for n, r in TOOL_RISK.items() if r == "approval"}
    assert approval == {"publish_assessment", "archive_assessment", "send_invitations", "retry_email", "retry_feedback"}
    forbidden = ("grade", "score", "delete", "passcode", "admin", "password")
    assert not [n for n in TOOL_RISK if any(word in n for word in forbidden)]


# ---------------------------------------------------------------------------
# Routing and read tools
# ---------------------------------------------------------------------------
def test_supervisor_routes_to_analytics_which_uses_tools(client, script):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers, title="Python Basics Quiz")
    script.route("analytics", "List the assessments.")
    script.call("analytics", "list_assessments")
    script.say("analytics", "There is one assessment: Python Basics Quiz.")
    script.finish("You have one assessment, Python Basics Quiz (active).")

    res = chat(client, headers, "What assessments do I have?")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    assert body["answer"] == "You have one assessment, Python Basics Quiz (active)."
    assert tools_called(body) == ["list_assessments"]
    result = next(s for s in body["trace"] if s["type"] == "tool_result")
    assert "Python Basics Quiz" in result["text"] and str(assessment["id"]) in result["text"]
    # The specialist received the supervisor's instruction.
    assert any("List the assessments." in seen for seen in script.seen["analytics"])


def test_multi_agent_handoff_analytics_then_coaching(client, make_client, mailbox, script):
    headers = setup_admin(client)
    full_flow(client, headers, make_client, mailbox, answers={0: {"selected": "B"}, 1: {"selected": "A"}})
    result_id = client.get("/api/admin/results", headers=headers).json()["items"][0]["result_id"]

    script.route("analytics", "Find Asha's result.")
    script.call("analytics", "list_results", candidate_name="Asha")
    script.say("analytics", f"Asha's result id is {result_id} (40%).")
    script.route("coaching", f"Explain what result {result_id} should work on.")
    script.call("coaching", "get_result_feedback", result_id=result_id)
    script.say("coaching", "Asha should practise exception handling.")
    script.finish("Asha scored 40%; she should practise exception handling.")

    body = chat(client, headers, "How did Asha do and what should she improve?").json()
    assert body["status"] == "completed"
    agents = [s["agent"] for s in body["trace"] if s["type"] == "tool_call"]
    assert agents == ["analytics", "coaching"]
    results = [s["text"] for s in body["trace"] if s["type"] == "tool_result"]
    assert "40.0" in results[0] and "asha@example.com" not in results[0]  # emails never shown in full
    assert "Exceptions" in results[1] or "exception" in results[1].lower()


# ---------------------------------------------------------------------------
# Writes: safe writes run, risky writes wait for approval
# ---------------------------------------------------------------------------
def test_safe_write_creates_a_draft_without_approval(client, script, db):
    headers = setup_admin(client)
    script.route("quiz_builder", "Create a draft called SQL Basics.")
    script.call("quiz_builder", "create_assessment", title="SQL Basics", category="SQL", access_start_at="2030-01-10T10:00", access_end_at="2030-01-10T13:00")
    script.say("quiz_builder", "Created the draft SQL Basics.")
    script.finish("Draft 'SQL Basics' created.")

    body = chat(client, headers, "Create a draft quiz called SQL Basics").json()
    assert body["status"] == "completed"
    created = db.scalar(select(Assessment).where(Assessment.title == "SQL Basics"))
    assert created.status == "draft" and created.created_by is not None
    assert created.access_start_at.isoformat().startswith("2030-01-10T04:30")  # 10:00 IST stored as UTC


def _invite_script(script, assessment_id, candidate_id):
    script.route("operations", "Invite the candidate.")
    script.call("operations", "send_invitations", assessment_id=assessment_id, candidate_ids=[candidate_id])
    script.say("operations", "Done.")
    script.finish("Finished.")


def test_invitation_waits_for_approval_then_sends(client, mailbox, script):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    candidate = create_candidate(client, headers, name="Neha Singh", email="neha@example.com")
    assign(client, headers, assessment["id"], candidate["id"], invite=False)
    _invite_script(script, assessment["id"], candidate["id"])

    first = chat(client, headers, "Invite Neha to the Python test").json()
    assert first["status"] == "needs_approval"
    pending = first["pending_approval"]
    assert pending["action"] == "send_invitations"
    assert "Neha Singh" in pending["summary"] and "ne***@example.com" in pending["summary"]
    assert invitation_emails(mailbox, "neha@example.com") == []  # nothing sent yet

    # A new message cannot skip the pending decision.
    assert chat(client, headers, "something else", first["thread_id"]).status_code == 409

    done = decide(client, headers, first["thread_id"], approved=True).json()
    assert done["status"] == "completed" and done["answer"] == "Finished."
    assert len(invitation_emails(mailbox, "neha@example.com")) == 1  # address came from the database
    result = next(s for s in done["trace"] if s["type"] == "tool_result")
    assert '"email_status": "sent"' in result["text"]


def test_rejected_action_is_not_executed(client, mailbox, script):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    candidate = create_candidate(client, headers)
    assign(client, headers, assessment["id"], candidate["id"], invite=False)
    _invite_script(script, assessment["id"], candidate["id"])

    first = chat(client, headers, "Invite Asha").json()
    done = decide(client, headers, first["thread_id"], approved=False, note="Not today").json()
    assert done["status"] == "completed"
    assert invitation_emails(mailbox, candidate["email"]) == []
    result = next(s for s in done["trace"] if s["type"] == "tool_result")
    assert "rejected_by_admin" in result["text"] and "Not today" in result["text"]


def test_publish_requires_approval(client, script, db):
    headers = setup_admin(client)
    draft = create_assessment(client, headers, publish=False, title="Draft Quiz")
    script.route("quiz_builder", "Publish it.")
    script.call("quiz_builder", "publish_assessment", assessment_id=draft["id"])
    script.say("quiz_builder", "Published.")
    script.finish("Published Draft Quiz.")

    first = chat(client, headers, "Publish Draft Quiz").json()
    assert first["pending_approval"]["action"] == "publish_assessment"
    assert db.get(Assessment, draft["id"]).status == "draft"
    decide(client, headers, first["thread_id"], approved=True)
    db.expire_all()
    assert db.get(Assessment, draft["id"]).status == "published"


def test_existing_service_rules_still_apply(client, script):
    """Agents use the same services as the API, so e.g. a published quiz stays locked."""
    headers = setup_admin(client)
    published = create_assessment(client, headers)
    question_id = published["questions"][0]["id"]
    script.route("quiz_builder", "Change question 1.")
    script.call("quiz_builder", "update_question", question_id=question_id, question_text="Changed?")
    script.say("quiz_builder", "That quiz is published, so its questions are locked.")
    script.finish("The quiz is published, so questions cannot be changed.")

    body = chat(client, headers, "Edit question 1 of the Python test").json()
    result = next(s for s in body["trace"] if s["type"] == "tool_result")
    assert '"error"' in result["text"] and "draft" in result["text"]
    detail = client.get(f"/api/assessments/{published['id']}", headers=headers).json()
    assert detail["questions"][0]["question_text"] != "Changed?"


# ---------------------------------------------------------------------------
# Access control and failures
# ---------------------------------------------------------------------------
def test_assistant_is_admin_only_and_conversations_are_private(client, make_client, mailbox, script):
    headers = setup_admin(client)
    assert client.post("/api/admin/agents/chat", json={"message": "hi"}).status_code == 401

    assessment = create_assessment(client, headers)
    candidate = create_candidate(client, headers)
    assign(client, headers, assessment["id"], candidate["id"], invite=False)
    _invite_script(script, assessment["id"], candidate["id"])
    first = chat(client, headers, "Invite Asha").json()

    make_admin(email="second-admin@example.com", password="Another-Passw0rd!")
    other = admin_headers(make_client(), "second-admin@example.com", "Another-Passw0rd!")
    assert decide(client, other, first["thread_id"], approved=True).status_code == 404
    assert invitation_emails(mailbox, candidate["email"]) == []


def test_decision_without_pending_action_is_rejected(client, script):
    headers = setup_admin(client)
    script.route("analytics", "Overview.")
    script.call("analytics", "get_overview")
    script.say("analytics", "All quiet.")
    script.finish("All quiet.")
    body = chat(client, headers, "Overview please").json()
    assert decide(client, headers, body["thread_id"], approved=True).status_code == 409


def test_follow_up_message_continues_the_conversation(client, script):
    headers = setup_admin(client)
    script.route("analytics", "Overview.").call("analytics", "get_overview").say("analytics", "0 assessments.").finish("You have no assessments yet.")
    first = chat(client, headers, "How many assessments?").json()
    script.finish("As I said, none yet.")
    second = chat(client, headers, "Are you sure?", first["thread_id"]).json()
    assert second["answer"] == "As I said, none yet."
    assert second["trace"] == [{"type": "message", "agent": "supervisor", "text": "As I said, none yet."}]


def test_unconfigured_openai_gives_a_clear_error(client, settings, monkeypatch, fake_ai):
    headers = setup_admin(client)
    ai_service.use_llm_factory(None)  # the real OpenAI factory
    monkeypatch.setattr(settings, "openai_api_key", "")
    res = chat(client, headers, "hello")
    assert res.status_code == 503 and "not configured" in res.json()["detail"]


def test_supervisor_stops_after_too_many_hand_offs(client, script):
    headers = setup_admin(client)
    for _ in range(supervisor_graph.MAX_DELEGATIONS):
        script.route("analytics", "again").say("analytics", "ok")
    body = chat(client, headers, "loop forever").json()
    assert body["status"] == "completed" and "avoid looping" in body["answer"]


def test_admin_email_is_never_returned_unmasked(client, script):
    headers = setup_admin(client)
    create_candidate(client, headers, name="Ravi Kumar", email="ravi.kumar@example.com")
    script.route("operations", "Find Ravi.").call("operations", "find_candidates", search="Ravi").say("operations", "Found Ravi.").finish("Found Ravi.")
    body = chat(client, headers, "Find Ravi").json()
    text = json.dumps(body)
    assert "ravi.kumar@example.com" not in text and "ra***@example.com" in text
    assert ADMIN_EMAIL not in text
