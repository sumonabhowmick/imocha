"""Candidate records and assessment assignments."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.database import utcnow
from app.errors import bad_request, conflict, not_found
from app.models import (
    AssessmentAssignment,
    Attempt,
    Candidate,
    EmailLog,
    Invitation,
    Result,
    User,
)
from app.schemas.candidate import CandidateCreate, CandidateUpdate
from app.services import auth_service
from app.services.assessment_service import ensure_not_archived, get_assessment


def create_candidate(db: Session, data: CandidateCreate) -> Candidate:
    email = auth_service.normalize_email(data.email)
    if db.scalar(select(Candidate).where(Candidate.email == email)):
        raise conflict("A candidate with this email already exists.")
    candidate = Candidate(name=data.name.strip(), email=email)
    db.add(candidate)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("A candidate with this email already exists.") from exc
    return candidate


def list_candidates(db: Session, search: str | None = None, active_only: bool = False) -> list[Candidate]:
    stmt = select(Candidate).order_by(Candidate.name)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(Candidate.name.ilike(pattern) | Candidate.email.ilike(pattern))
    if active_only:
        stmt = stmt.where(Candidate.is_active.is_(True))
    return list(db.scalars(stmt))


def update_candidate(db: Session, candidate_id: int, data: CandidateUpdate) -> Candidate:
    """Edit name, email or active status.

    Deactivating blocks new access but keeps every historical result. When the email
    changes, invitations that were sent to the old address but never used are revoked
    (so the old inbox can no longer start the assessment); a student who has already
    verified keeps their session. Resend the invitation to reach the new address.
    """
    from app.services.invitation_service import revoke_unused_invitations

    candidate = db.get(Candidate, candidate_id)
    if not candidate:
        raise not_found("Candidate")
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"]:
        candidate.name = changes["name"].strip()
    if changes.get("email"):
        new_email = auth_service.normalize_email(changes["email"])
        if new_email != candidate.email:
            taken = db.scalar(select(Candidate.id).where(Candidate.email == new_email, Candidate.id != candidate.id))
            if taken:
                raise conflict("Another candidate already uses this email.")
            candidate.email = new_email
            revoke_unused_invitations(db, candidate.id)
    if "is_active" in changes and changes["is_active"] is not None:
        candidate.is_active = changes["is_active"]
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("Another candidate already uses this email.") from exc
    return candidate


def delete_candidate(db: Session, candidate_id: int) -> None:
    """Delete a candidate who has never started an assessment.

    Their assignments and unused invitations are removed with them (email delivery
    records are kept, unlinked). Anyone with an attempt has results that must be kept,
    so they can only be deactivated.
    """
    candidate = db.get(Candidate, candidate_id, with_for_update=True)
    if not candidate:
        raise not_found("Candidate")
    assignment_ids = list(
        db.scalars(select(AssessmentAssignment.id).where(AssessmentAssignment.candidate_id == candidate_id))
    )
    if assignment_ids and db.scalar(select(func.count(Attempt.id)).where(Attempt.assignment_id.in_(assignment_ids))):
        raise conflict(
            "This candidate has already taken an assessment, so their results must be kept. "
            "Deactivate the candidate instead.",
            "candidate_has_results",
        )
    for assignment in db.scalars(select(AssessmentAssignment).where(AssessmentAssignment.id.in_(assignment_ids))):
        db.delete(assignment)  # invitations and their student sessions are deleted by cascade
    db.flush()
    db.delete(candidate)
    db.commit()


def assign_candidates(
    db: Session, assessment_id: int, candidate_ids: list[int], max_attempts: int | None, admin: User
) -> list[AssessmentAssignment]:
    """Create assignments; existing assignments are returned unchanged (no duplicates)."""
    assessment = get_assessment(db, assessment_id)
    ensure_not_archived(assessment)
    unique_ids = list(dict.fromkeys(candidate_ids))
    candidates = {c.id: c for c in db.scalars(select(Candidate).where(Candidate.id.in_(unique_ids)))}
    missing = [cid for cid in unique_ids if cid not in candidates]
    if missing:
        raise bad_request(f"Unknown candidate ids: {missing}")
    inactive = [cid for cid, c in candidates.items() if not c.is_active]
    if inactive:
        raise bad_request(f"Inactive candidates cannot be assigned: {inactive}")

    existing = {
        a.candidate_id: a
        for a in db.scalars(
            select(AssessmentAssignment).where(
                AssessmentAssignment.assessment_id == assessment_id,
                AssessmentAssignment.candidate_id.in_(unique_ids),
            )
        )
    }
    assignments = []
    for cid in unique_ids:
        if cid in existing:
            assignments.append(existing[cid])
            continue
        assignment = AssessmentAssignment(
            assessment_id=assessment_id,
            candidate_id=cid,
            max_attempts=max_attempts or assessment.max_attempts,
            status="assigned",
            created_by=admin.id,
        )
        db.add(assignment)
        assignments.append(assignment)
    try:
        db.commit()
    except IntegrityError as exc:  # concurrent duplicate assignment
        db.rollback()
        raise conflict("One of these candidates was assigned concurrently. Please retry.") from exc
    return assignments


def get_assignment(db: Session, assignment_id: int) -> AssessmentAssignment:
    assignment = db.scalar(
        select(AssessmentAssignment)
        .where(AssessmentAssignment.id == assignment_id)
        .options(joinedload(AssessmentAssignment.candidate), joinedload(AssessmentAssignment.assessment))
    )
    if not assignment:
        raise not_found("Assignment")
    return assignment


def revoke_assignment(db: Session, assignment_id: int) -> AssessmentAssignment:
    """Withdraw an assignment: invitations and student sessions stop working."""
    from app.services.invitation_service import revoke_usable_invitations

    assignment = get_assignment(db, assignment_id)
    if assignment.status == "completed":
        raise conflict("Completed assignments cannot be revoked; their results are kept.")
    assignment.status = "revoked"
    revoke_usable_invitations(db, assignment.id)
    db.commit()
    return assignment


def _passcode_status(inv: Invitation | None) -> str | None:
    if inv is None:
        return None
    now = utcnow()
    if inv.locked_until and inv.locked_until > now:
        return "locked"
    if inv.passcode_consumed_at:
        return "consumed"
    if not inv.passcode_hash:
        return "invalidated"
    if inv.passcode_expires_at and inv.passcode_expires_at <= now:
        return "expired"
    return "pending"


def _invitation_status(inv: Invitation | None) -> str | None:
    if inv is None:
        return None
    if inv.status in ("active", "verified") and inv.invitation_expires_at <= utcnow():
        return "expired"
    return inv.status


def assignment_rows(db: Session, assessment_id: int | None = None) -> list[dict]:
    """Assignment overview rows for the dashboard (never includes codes or tokens)."""
    stmt = (
        select(AssessmentAssignment)
        .options(joinedload(AssessmentAssignment.candidate), joinedload(AssessmentAssignment.assessment))
        .order_by(AssessmentAssignment.assigned_at.desc())
    )
    if assessment_id is not None:
        stmt = stmt.where(AssessmentAssignment.assessment_id == assessment_id)
    assignments = list(db.scalars(stmt).unique())
    if not assignments:
        return []
    ids = [a.id for a in assignments]

    latest_inv: dict[int, Invitation] = {}
    for inv in db.scalars(
        select(Invitation).where(Invitation.assignment_id.in_(ids)).order_by(Invitation.created_at)
    ):
        latest_inv[inv.assignment_id] = inv
    attempts_used = dict(
        db.execute(
            select(Attempt.assignment_id, func.count(Attempt.id))
            .where(Attempt.assignment_id.in_(ids))
            .group_by(Attempt.assignment_id)
        ).all()
    )
    latest_email: dict[int, str] = {}
    for log in db.scalars(
        select(EmailLog)
        .where(EmailLog.related_assignment_id.in_(ids), EmailLog.notification_type.in_(("invitation", "passcode")))
        .order_by(EmailLog.created_at)
    ):
        latest_email[log.related_assignment_id] = log.status
    latest_result: dict[int, Result] = {}
    for assignment_id, result in db.execute(
        select(Attempt.assignment_id, Result)
        .join(Result, Result.attempt_id == Attempt.id)
        .where(Attempt.assignment_id.in_(ids))
        .order_by(Attempt.attempt_number)
    ).all():
        latest_result[assignment_id] = result

    rows = []
    for a in assignments:
        inv = latest_inv.get(a.id)
        result = latest_result.get(a.id)
        rows.append(
            {
                "id": a.id,
                "assessment_id": a.assessment_id,
                "assessment_title": a.assessment.title,
                "candidate_id": a.candidate_id,
                "candidate_name": a.candidate.name,
                "candidate_email": a.candidate.email,
                "status": a.status,
                "max_attempts": a.max_attempts,
                "attempts_used": attempts_used.get(a.id, 0),
                "assigned_at": a.assigned_at,
                "invitation_status": _invitation_status(inv),
                "passcode_status": _passcode_status(inv),
                "invitation_expires_at": inv.invitation_expires_at if inv else None,
                "last_email_status": latest_email.get(a.id),
                "result_id": result.id if result else None,
                "percentage": float(result.percentage) if result and result.percentage is not None else None,
            }
        )
    return rows
