"""Operations Agent tools. They wrap the services behind the Candidates & invitations
and Email status pages.

Email addresses never come from the model: tools work with candidate ids, and the
existing invitation service reads the address from the database. Sending invitations
and retrying emails require admin approval.
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.tooling import admin_from_config, agent_tool, mask_email, request_approval
from app.models import AssessmentAssignment, Candidate, EmailLog
from app.schemas.candidate import CandidateCreate
from app.services import candidate_service, invitation_service
from app.services.assessment_service import get_assessment


@agent_tool("find_candidates", "read")
def find_candidates(db: Session, config: RunnableConfig | None, search: str = "", active_only: bool = False) -> list[dict]:
    """Find candidates by part of their name or email. Returns id, name, masked email and
    whether they are active. Use the id with other tools."""
    return [
        {"id": c.id, "name": c.name, "email": mask_email(c.email), "is_active": c.is_active}
        for c in candidate_service.list_candidates(db, search or None, active_only)[:100]
    ]


@agent_tool("create_candidate", "write")
def create_candidate(db: Session, config: RunnableConfig | None, name: str, email: str) -> dict:
    """Add a new candidate (student). Only use an email address the admin typed in the
    conversation. Duplicate emails are refused."""
    candidate = candidate_service.create_candidate(db, CandidateCreate(name=name, email=email))
    return {"created": {"id": candidate.id, "name": candidate.name, "email": mask_email(candidate.email)}}


@agent_tool("assign_candidates", "write")
def assign_candidates(db: Session, config: RunnableConfig | None, assessment_id: int, candidate_ids: list[int]) -> dict:
    """Assign an assessment to candidates (by id). Does NOT send emails - use
    send_invitations for that. Existing assignments are left unchanged."""
    if not candidate_ids:
        raise ValueError("Give at least one candidate id.")
    admin = admin_from_config(db, config)
    assignments = candidate_service.assign_candidates(db, assessment_id, candidate_ids, None, admin)
    return {"assignment_ids": [a.id for a in assignments], "assessment_id": assessment_id}


@agent_tool("list_assignments", "read")
def list_assignments(db: Session, config: RunnableConfig | None, assessment_id: int) -> list[dict]:
    """Who is assigned to an assessment, with assignment status, invitation status,
    passcode status (never the code), last email status, attempts used and score."""
    rows = candidate_service.assignment_rows(db, assessment_id)
    for r in rows:
        r["candidate_email"] = mask_email(r["candidate_email"])
    return rows


@agent_tool("send_invitations", "approval")
def send_invitations(db: Session, config: RunnableConfig | None, assessment_id: int, candidate_ids: list[int]) -> dict:
    """Email secure invitations (Start link + one-time code) for an assessment to
    candidates who are already assigned to it. The address stored for each candidate is
    used. REQUIRES ADMIN APPROVAL."""
    if not candidate_ids:
        raise ValueError("Give at least one candidate id.")
    assessment = get_assessment(db, assessment_id)
    rows = db.execute(
        select(AssessmentAssignment, Candidate)
        .join(Candidate, AssessmentAssignment.candidate_id == Candidate.id)
        .where(AssessmentAssignment.assessment_id == assessment_id, AssessmentAssignment.candidate_id.in_(candidate_ids))
    ).all()
    found = {c.id for _a, c in rows}
    missing = [cid for cid in candidate_ids if cid not in found]
    if missing:
        raise ValueError(f"Candidates {missing} are not assigned to this assessment yet; call assign_candidates first.")
    recipients = [{"candidate_id": c.id, "name": c.name, "email": mask_email(c.email)} for _a, c in rows]
    decision = request_approval(
        "send_invitations",
        f"Email invitations for '{assessment.title}' to {len(recipients)} candidate(s): "
        + ", ".join(f"{r['name']} ({r['email']})" for r in recipients),
        {"assessment_id": assessment_id, "recipients": recipients},
    )
    if not decision.get("approved"):
        return {"status": "rejected_by_admin", "note": decision.get("note", "")}
    sent = []
    for assignment, candidate in rows:
        try:
            issued = invitation_service.issue_invitation(db, assignment.id)
            sent.append({"name": candidate.name, "email_status": issued.email_log.status, "error": issued.email_log.last_error})
        except Exception as exc:  # report per candidate, keep going
            db.rollback()
            sent.append({"name": candidate.name, "email_status": "not_sent", "error": getattr(exc, "message", type(exc).__name__)})
    return {"invitations": sent}


@agent_tool("list_email_logs", "read")
def list_email_logs(db: Session, config: RunnableConfig | None, status: str | None = None, limit: int = 30) -> list[dict]:
    """Recent email deliveries (invitation, passcode, result, admin notices) with status
    (queued/sent/failed/outbox), attempts and the last error. Filter by status."""
    stmt = select(EmailLog).order_by(EmailLog.created_at.desc()).limit(max(1, min(limit, 200)))
    if status:
        stmt = stmt.where(EmailLog.status == status)
    return [
        {
            "id": log.id,
            "type": log.notification_type,
            "recipient": mask_email(log.recipient_email),
            "subject": log.subject,
            "status": log.status,
            "attempts": log.attempts,
            "last_error": log.last_error,
            "created_at": log.created_at,
        }
        for log in db.scalars(stmt)
    ]


@agent_tool("retry_email", "approval")
def retry_email(db: Session, config: RunnableConfig | None, email_log_id: int) -> dict:
    """Retry a FAILED email. For invitations a fresh link and code are issued (the old
    ones stop working). REQUIRES ADMIN APPROVAL."""
    from app.workflows.assessment_workflow import retry_email as workflow_retry_email

    log = db.get(EmailLog, email_log_id)
    if log is None:
        raise ValueError("Email log not found.")
    decision = request_approval(
        "retry_email",
        f"Retry the {log.notification_type} email to {mask_email(log.recipient_email)} (currently {log.status}).",
        {"email_log_id": email_log_id, "type": log.notification_type},
    )
    if not decision.get("approved"):
        return {"status": "rejected_by_admin", "note": decision.get("note", "")}
    result = workflow_retry_email(db, email_log_id)
    return {"email_log_id": result.id, "status": result.status, "error": result.last_error}
