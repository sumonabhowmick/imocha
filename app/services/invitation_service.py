"""Secure email invitations and one-time passcodes.

* The invitation token (in the link) and the passcode (typed by the student) are two
  independent CSPRNG values. Only keyed HMAC hashes of them are stored.
* Passcodes expire quickly, are single-use, and lock after repeated failures.
* Re-issuing an invitation or a code invalidates the previous credentials.

Note: the link and the code are delivered to the same inbox, so this is email-based
access verification, not independent multi-factor authentication.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import utcnow
from app.errors import AppError, conflict
from app.models import AssessmentAssignment, EmailLog, Invitation
from app.security import (
    constant_time_equals,
    generate_passcode,
    generate_token,
    hash_invitation_token,
    hash_passcode,
    rate_limiter,
)
from app.services import auth_service, email_service
from app.services.candidate_service import get_assignment

logger = logging.getLogger(__name__)

INVALID_CODE = "The code is incorrect or has expired. Check your email or request a new code."
INVALID_INVITATION = "This invitation link is invalid or no longer active. Contact your administrator."
RESEND_GENERIC = "If this invitation is still valid, a new code has been sent to the invited email address."


@dataclass
class IssuedInvitation:
    invitation: Invitation
    email_log: EmailLog


def revoke_usable_invitations(db: Session, assignment_id: int) -> None:
    """Revoke active/verified invitations for an assignment and their student sessions."""
    usable = list(
        db.scalars(
            select(Invitation).where(
                Invitation.assignment_id == assignment_id, Invitation.status.in_(("active", "verified"))
            )
        )
    )
    for inv in usable:
        inv.status = "revoked"
        inv.passcode_hash = None
        auth_service.revoke_student_sessions(db, inv.id)
    db.flush()


def issue_invitation(db: Session, assignment_id: int, log: EmailLog | None = None) -> IssuedInvitation:
    """Create a new invitation (revoking older ones) and email it to the candidate."""
    settings = get_settings()
    assignment = get_assignment(db, assignment_id)
    db.execute(
        select(AssessmentAssignment.id).where(AssessmentAssignment.id == assignment_id).with_for_update()
    )
    assessment = assignment.assessment
    candidate = assignment.candidate
    now = utcnow()
    if assignment.status == "revoked":
        raise conflict("This assignment has been revoked.")
    if not candidate.is_active:
        raise conflict("The candidate is inactive.")
    if assessment.status != "published":
        raise conflict("Publish the assessment before sending invitations.")
    if assessment.access_end_at and assessment.access_end_at <= now:
        raise conflict("The assessment's access window has already closed.")
    if len(assignment.attempts) >= assignment.max_attempts:
        raise conflict("This candidate has already used every permitted attempt.")

    revoke_usable_invitations(db, assignment.id)

    token = generate_token()
    passcode = generate_passcode()
    # The invitation stays valid at least until the access window closes.
    expires = now + timedelta(minutes=settings.invitation_ttl_minutes)
    if assessment.access_end_at and assessment.access_end_at > expires:
        expires = assessment.access_end_at
    invitation = Invitation(
        assignment_id=assignment.id,
        invitation_token_hash=hash_invitation_token(token),
        passcode_expires_at=now + timedelta(minutes=settings.passcode_ttl_minutes),
        invitation_expires_at=expires,
        status="active",
        last_code_sent_at=now,
    )
    db.add(invitation)
    db.flush()  # need invitation.id to bind the passcode hash to it
    invitation.passcode_hash = hash_passcode(invitation.id, passcode)
    if assignment.status == "assigned":
        assignment.status = "invited"
    db.commit()
    logger.info("Invitation %s issued for assignment %s", invitation.id, assignment.id)

    # Raw token/passcode exist only in memory for the email and are then discarded.
    email_log = email_service.send_invitation_email(db, assignment, token, passcode, log=log)
    return IssuedInvitation(invitation, email_log)


def _find_invitation(db: Session, token: str, for_update: bool = False) -> Invitation | None:
    stmt = select(Invitation).where(Invitation.invitation_token_hash == hash_invitation_token(token))
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def _invitation_usable(db: Session, inv: Invitation | None) -> AssessmentAssignment | None:
    """Return the assignment if the invitation can still be used, else None."""
    if inv is None or inv.status not in ("active", "verified"):
        return None
    now = utcnow()
    if inv.invitation_expires_at <= now:
        inv.status = "expired"
        inv.passcode_hash = None
        db.commit()
        return None
    assignment = get_assignment(db, inv.assignment_id)
    if assignment.status == "revoked" or not assignment.candidate.is_active:
        return None
    if assignment.assessment.status == "archived":
        return None
    return assignment


def verify_passcode(db: Session, token: str, passcode: str, client_ip: str) -> tuple[str, str]:
    """Check the token + passcode and open a student session.

    Returns (session_token, csrf_token). Raises AppError with safe messages.
    """
    settings = get_settings()
    if not rate_limiter.hit(f"verify-ip:{client_ip}", 30, 600):
        raise AppError(429, "Too many attempts. Please wait a few minutes.", "rate_limited")

    inv = _find_invitation(db, token, for_update=True)
    assignment = _invitation_usable(db, inv)
    if assignment is None:
        raise AppError(400, INVALID_INVITATION, "invalid_invitation")

    now = utcnow()
    if inv.locked_until and inv.locked_until > now:
        raise AppError(
            429,
            "Too many incorrect codes. Please wait a few minutes, then request a new code.",
            "locked",
        )
    if not inv.passcode_hash or inv.passcode_consumed_at is not None:
        raise AppError(400, "This code has already been used or replaced. Request a new code.", "code_used")
    if inv.passcode_expires_at is None or inv.passcode_expires_at <= now:
        raise AppError(400, "This code has expired. Request a new code.", "code_expired")

    if not constant_time_equals(hash_passcode(inv.id, passcode), inv.passcode_hash):
        inv.failed_attempts += 1
        if inv.failed_attempts >= settings.passcode_max_attempts:
            # Lock and burn the current code: the student must wait, then request a new one.
            inv.locked_until = now + timedelta(minutes=settings.passcode_lockout_minutes)
            inv.passcode_hash = None
            inv.failed_attempts = 0
            db.commit()
            logger.warning("Invitation %s locked after repeated passcode failures", inv.id)
            raise AppError(
                429,
                "Too many incorrect codes. Please wait a few minutes, then request a new code.",
                "locked",
            )
        db.commit()
        raise AppError(400, INVALID_CODE, "invalid_code")

    # Success: consume the code (prevents replay) and open a fresh session.
    inv.passcode_consumed_at = now
    inv.failed_attempts = 0
    inv.locked_until = None
    inv.status = "verified"
    session_token, csrf_token = auth_service.create_student_session(db, inv.id)
    db.commit()
    logger.info("Invitation %s verified", inv.id)
    return session_token, csrf_token


def resend_code(db: Session, token: str, client_ip: str) -> str:
    """Issue a new passcode for a valid invitation. Always returns the same generic message
    so the endpoint cannot be used to probe which invitations exist."""
    settings = get_settings()
    if not rate_limiter.hit(f"resend-ip:{client_ip}", 10, 600):
        raise AppError(429, "Too many requests. Please wait a few minutes.", "rate_limited")

    inv = _find_invitation(db, token, for_update=True)
    assignment = _invitation_usable(db, inv)
    if assignment is None:
        return RESEND_GENERIC
    now = utcnow()
    if inv.locked_until and inv.locked_until > now:
        return RESEND_GENERIC
    if inv.last_code_sent_at and (now - inv.last_code_sent_at).total_seconds() < settings.passcode_resend_cooldown_seconds:
        return RESEND_GENERIC
    if inv.resend_count >= settings.passcode_max_resends:
        logger.info("Invitation %s reached the passcode resend limit", inv.id)
        return RESEND_GENERIC

    passcode = generate_passcode()
    inv.passcode_hash = hash_passcode(inv.id, passcode)  # replaces (invalidates) the old code
    inv.passcode_expires_at = now + timedelta(minutes=settings.passcode_ttl_minutes)
    inv.passcode_consumed_at = None
    inv.failed_attempts = 0
    inv.resend_count += 1
    inv.last_code_sent_at = now
    db.commit()
    email_service.send_passcode_email(db, assignment, passcode)
    return RESEND_GENERIC


def expire_old_invitations(db: Session) -> int:
    """Housekeeping: mark invitations past their validity as expired."""
    res = db.execute(
        update(Invitation)
        .where(Invitation.status.in_(("active", "verified")), Invitation.invitation_expires_at <= utcnow())
        .values(status="expired", passcode_hash=None)
    )
    db.commit()
    return res.rowcount or 0
