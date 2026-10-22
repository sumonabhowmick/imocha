from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel


class CandidateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr


class CandidateUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    email: EmailStr | None = None
    is_active: bool | None = None


class CandidateOut(ORMModel):
    id: int
    name: str
    email: str
    is_active: bool
    created_at: datetime


class AssignRequest(BaseModel):
    candidate_ids: list[int] = Field(min_length=1, max_length=500)
    max_attempts: int | None = Field(None, ge=1, le=10)
    send_invitations: bool = False


class AssignmentOut(BaseModel):
    id: int
    assessment_id: int
    assessment_title: str
    candidate_id: int
    candidate_name: str
    candidate_email: str
    status: str
    max_attempts: int
    attempts_used: int
    assigned_at: datetime
    invitation_status: str | None  # active / verified / revoked / expired / None
    passcode_status: str | None  # pending / consumed / expired / locked / invalidated / None
    invitation_expires_at: datetime | None
    last_email_status: str | None
    result_id: int | None
    percentage: float | None


class InviteResponse(BaseModel):
    assignment_id: int
    email_status: str
    email_log_id: int
    message: str
