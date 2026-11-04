"""Admin-only multi-agent assistant endpoints.

The assistant acts with the permissions of the logged-in admin, through the same
service functions as the rest of the API. Risky actions pause until the admin approves.
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.agents import supervisor_graph
from app.services import audit_service
from app.dependencies import agent_rate_limited, client_ip, get_current_admin
from app.models import User

router = APIRouter(prefix="/api/admin/agents", tags=["agents"], dependencies=[Depends(get_current_admin)])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(None, max_length=100)


class DecisionRequest(BaseModel):
    approved: bool
    note: str = Field("", max_length=500)


@router.get("")
def list_agents():
    """The supervisor's specialist agents and their tools (with risk levels)."""
    return supervisor_graph.describe_agents()


@router.post("/chat")
def chat(body: ChatRequest, admin: User = Depends(agent_rate_limited)):
    """Send a message. Returns the answer, or a pending action that needs approval."""
    return supervisor_graph.chat(admin.id, body.message.strip(), body.thread_id)


@router.post("/{thread_id}/decision")
def decide(thread_id: str, body: DecisionRequest, request: Request, admin: User = Depends(agent_rate_limited)):
    """Approve or reject the action the assistant is waiting on; the agents then continue."""
    out = supervisor_graph.decide(admin.id, thread_id, body.approved, body.note)
    audit_service.admin(admin, "agent.approve" if body.approved else "agent.reject", details={"thread_id": thread_id}, ip=client_ip(request))
    return out
