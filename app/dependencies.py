"""FastAPI dependencies for authentication and authorization.

The backend is the security boundary: every protected route depends on one of these.
Student identity is always derived from the server-side session, never from IDs sent
by the client.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError
from app.models import AssessmentAssignment, Assessment, AuthSession, Candidate, Invitation, User
from app.security import constant_time_equals, hash_session_token, rate_limiter
from app.services import auth_service

STUDENT_COOKIE = "af_student"
CSRF_COOKIE = "af_csrf"
CSRF_HEADER = "X-CSRF-Token"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


ADMIN_COOKIE = "af_admin"
ADMIN_CSRF_COOKIE = "af_admin_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_current_admin(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Admins authenticate with a bearer token (API clients, tests) or, in the browser
    admin app, with an HttpOnly session cookie. Cookie requests that change data must
    also send the session's CSRF token in the X-CSRF-Token header."""
    bearer = _bearer_token(authorization)
    if bearer:
        user = auth_service.get_admin_for_token(db, bearer)
    else:
        session = auth_service.get_active_session(db, request.cookies.get(ADMIN_COOKIE), "admin")
        user = None
        if session:
            if request.method not in SAFE_METHODS:
                supplied = request.headers.get(CSRF_HEADER)
                if not supplied or not constant_time_equals(hash_session_token(supplied), session.csrf_token_hash):
                    raise AppError(403, "Invalid or missing CSRF token. Please reload the page.", "csrf_failed")
            user = db.get(User, session.user_id)
            if user and (not user.is_active or user.role != "admin"):
                user = None
    if not user:
        raise AppError(401, "Authentication required.", "unauthenticated")
    return user


def ai_rate_limited(admin: User = Depends(get_current_admin)) -> User:
    """Caps AI-backed admin actions per admin (protects the OpenAI bill if a session leaks
    or a script misbehaves)."""
    from app.config import get_settings

    if not rate_limiter.hit(f"ai-admin:{admin.id}", get_settings().ai_actions_per_10_minutes, 600):
        raise AppError(429, "Too many AI requests. Please wait a few minutes.", "rate_limited")
    return admin


def agent_rate_limited(admin: User = Depends(get_current_admin)) -> User:
    from app.config import get_settings

    if not rate_limiter.hit(f"agent-admin:{admin.id}", get_settings().agent_messages_per_10_minutes, 600):
        raise AppError(429, "Too many assistant messages. Please wait a few minutes.", "rate_limited")
    return admin


@dataclass
class StudentContext:
    session: AuthSession
    invitation: Invitation
    assignment: AssessmentAssignment
    assessment: Assessment
    candidate: Candidate


def get_student_context(request: Request, db: Session = Depends(get_db)) -> StudentContext:
    session = auth_service.get_active_session(db, request.cookies.get(STUDENT_COOKIE), "student")
    if not session:
        raise AppError(
            401,
            "Your assessment session has expired or is invalid. Open the link from your invitation email to sign in again.",
            "unauthenticated",
        )
    invitation = db.get(Invitation, session.invitation_id)
    if not invitation or invitation.status != "verified":
        raise AppError(401, "This invitation is no longer valid.", "unauthenticated")
    assignment = db.get(AssessmentAssignment, invitation.assignment_id)
    if not assignment or assignment.status == "revoked":
        raise AppError(403, "This assessment is no longer assigned to you.", "forbidden")
    candidate = db.get(Candidate, assignment.candidate_id)
    if not candidate or not candidate.is_active:
        raise AppError(403, "Your access to this assessment has been disabled.", "forbidden")
    assessment = db.get(Assessment, assignment.assessment_id)
    return StudentContext(session, invitation, assignment, assessment, candidate)


def require_student_csrf(
    request: Request,
    ctx: StudentContext = Depends(get_student_context),
) -> StudentContext:
    """CSRF protection for cookie-authenticated, state-changing student requests.

    The CSRF token is returned once at verification and must be echoed in a header.
    A cross-site attacker can make the browser send the cookie but cannot read or set
    this header. (The cookie is also SameSite=Strict as a second layer.)
    """
    supplied = request.headers.get(CSRF_HEADER)
    if not supplied or not constant_time_equals(hash_session_token(supplied), ctx.session.csrf_token_hash):
        raise AppError(403, "Invalid or missing CSRF token. Please reload the page.", "csrf_failed")
    return ctx
