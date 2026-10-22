"""Admin-only multi-agent assistant endpoints.

The assistant acts with the permissions of the logged-in admin, through the same
service functions as the rest of the API. Risky actions pause until the admin approves.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.agents import supervisor_graph
from app.dependencies import get_current_admin
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
def chat(body: ChatRequest, admin: User = Depends(get_current_admin)):
    """Send a message. Returns the answer, or a pending action that needs approval."""
    return supervisor_graph.chat(admin.id, body.message.strip(), body.thread_id)


@router.post("/{thread_id}/decision")
def decide(thread_id: str, body: DecisionRequest, admin: User = Depends(get_current_admin)):
    """Approve or reject the action the assistant is waiting on; the agents then continue."""
    return supervisor_graph.decide(admin.id, thread_id, body.approved, body.note)
