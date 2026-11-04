"""Student portal API.

Identity always comes from the server-side session cookie (email-code login or an
invitation link). Every assignment, attempt and result id in a URL is checked against
that student; anything that is not theirs looks "not found". State-changing requests
require the CSRF token header.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db, utcnow
from app.dependencies import CSRF_COOKIE, CSRF_HEADER, STUDENT_COOKIE, StudentContext, client_ip
from app.errors import AppError
from app.models import Assessment, AssessmentAssignment, AuthSession, Candidate, Result
from app.schemas.student import AnswerIn, AnswerSaved, AttemptOut, AttemptQuestions, AttemptStatus, SubmitResponse
from app.security import constant_time_equals, hash_session_token, rate_limiter
from app.services import attempt_service, audit_service, auth_service, portal_service, proctoring_service
from app.workflows.assessment_workflow import process_attempt

router = APIRouter(prefix="/api/portal", tags=["student portal"])


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------
@dataclass
class PortalUser:
    session: AuthSession
    candidate: Candidate


def get_portal_user(request: Request, db: Session = Depends(get_db)) -> PortalUser:
    session = auth_service.get_portal_session(db, request.cookies.get(STUDENT_COOKIE))
    if not session:
        raise AppError(401, "Please sign in to the student portal.", "unauthenticated")
    return PortalUser(session, portal_service.candidate_for_session(db, session))


def require_portal_csrf(request: Request, user: PortalUser = Depends(get_portal_user)) -> PortalUser:
    supplied = request.headers.get(CSRF_HEADER)
    if not supplied or not constant_time_equals(hash_session_token(supplied), user.session.csrf_token_hash):
        raise AppError(403, "Invalid or missing CSRF token. Please reload the page.", "csrf_failed")
    return user


def _context(db: Session, user: PortalUser, assignment: AssessmentAssignment) -> StudentContext:
    assessment = db.get(Assessment, assignment.assessment_id)
    if assessment.status == "draft":
        raise AppError(404, "Assessment not found.", "not_found")
    return StudentContext(user.session, None, assignment, assessment, user.candidate)


def _attempt_context(db: Session, user: PortalUser, attempt_id: int) -> StudentContext:
    return _context(db, user, portal_service.owned_attempt_assignment(db, user.candidate, attempt_id))


def _set_cookies(response: Response, token: str, csrf: str) -> None:
    cookie = dict(secure=get_settings().is_production, samesite="strict", path="/")
    response.set_cookie(STUDENT_COOKIE, token, httponly=True, **cookie)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **cookie)


# ---------------------------------------------------------------------------
# Sign in / out
# ---------------------------------------------------------------------------
class CodeRequest(BaseModel):
    email: EmailStr


class CodeVerify(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12, pattern=r"^\d+$")


class StartRequest(BaseModel):
    proctoring_consent: bool = False


class ProctoringEventIn(BaseModel):
    event_type: str = Field(min_length=3, max_length=30)
    duration_seconds: int | None = Field(None, ge=0, le=86_400)
    details: dict | None = None
    snapshot: str | None = Field(None, max_length=220_000)  # base64 JPEG (data URL allowed)


@router.post("/login/request-code")
def request_code(body: CodeRequest, request: Request, db: Session = Depends(get_db)):
    return {"message": portal_service.request_login_code(db, body.email, client_ip(request))}


@router.post("/login/verify")
def verify_code(body: CodeVerify, request: Request, response: Response, db: Session = Depends(get_db)):
    candidate, token, csrf = portal_service.verify_login_code(db, body.email, body.code, client_ip(request))
    _set_cookies(response, token, csrf)
    audit_service.record("portal.login", actor_type="candidate", actor_id=candidate.id, actor_label=candidate.email, ip=client_ip(request))
    return {"name": candidate.name, "email": candidate.email, "csrf_token": csrf}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    session = auth_service.get_portal_session(db, request.cookies.get(STUDENT_COOKIE))
    if session:
        session.revoked_at = utcnow()
        db.commit()
    response.delete_cookie(STUDENT_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"message": "Signed out."}


@router.get("/me")
def me(user: PortalUser = Depends(get_portal_user)):
    return {"name": user.candidate.name, "email": user.candidate.email}


# ---------------------------------------------------------------------------
# Assessments and attempts
# ---------------------------------------------------------------------------
@router.get("/assessments")
def my_assessments(user: PortalUser = Depends(get_portal_user), db: Session = Depends(get_db)):
    return portal_service.assessments_for(db, user.candidate)


@router.get("/assessments/{assignment_id}")
def assessment_overview(
    assignment_id: int, background: BackgroundTasks, user: PortalUser = Depends(get_portal_user), db: Session = Depends(get_db)
):
    ctx = _context(db, user, portal_service.owned_assignment(db, user.candidate, assignment_id))
    data, finalized = attempt_service.overview(db, ctx)
    for attempt_id in finalized:
        background.add_task(process_attempt, attempt_id)
    return {**data, "assignment_id": assignment_id, "proctoring_mode": "required"}


@router.post("/assessments/{assignment_id}/attempts", response_model=AttemptOut)
def start_attempt(
    assignment_id: int, body: StartRequest, user: PortalUser = Depends(require_portal_csrf), db: Session = Depends(get_db)
):
    ctx = _context(db, user, portal_service.owned_assignment(db, user.candidate, assignment_id))
    # Camera proctoring is mandatory; start_attempt refuses without consent.
    attempt, _created = attempt_service.start_attempt(db, ctx, proctoring_consent=body.proctoring_consent)
    return AttemptOut(
        attempt_id=attempt.id,
        attempt_number=attempt.attempt_number,
        status=attempt.status,
        started_at=attempt.started_at,
        deadline_at=attempt.deadline_at,
        remaining_seconds=attempt_service.remaining_seconds(attempt),
    )


@router.get("/attempts/{attempt_id}/questions", response_model=AttemptQuestions)
def attempt_questions(attempt_id: int, user: PortalUser = Depends(get_portal_user), db: Session = Depends(get_db)):
    return attempt_service.questions_for_attempt(db, _attempt_context(db, user, attempt_id), attempt_id)


@router.put("/attempts/{attempt_id}/answers/{question_id}", response_model=AnswerSaved)
def save_answer(
    attempt_id: int, question_id: int, body: AnswerIn, user: PortalUser = Depends(require_portal_csrf), db: Session = Depends(get_db)
):
    if not rate_limiter.hit(f"answer-save:{attempt_id}", 400, 600):
        raise AppError(429, "Too many saves. Please slow down.", "rate_limited")
    attempt = attempt_service.save_answer(db, _attempt_context(db, user, attempt_id), attempt_id, question_id, body.selected, body.text)
    return AnswerSaved(question_id=question_id, saved=True, remaining_seconds=attempt_service.remaining_seconds(attempt))


@router.post("/attempts/{attempt_id}/submit", response_model=SubmitResponse)
def submit(
    attempt_id: int, background: BackgroundTasks, user: PortalUser = Depends(require_portal_csrf), db: Session = Depends(get_db)
):
    attempt, newly = attempt_service.submit_attempt(db, _attempt_context(db, user, attempt_id), attempt_id)
    if db.scalar(select(Result.id).where(Result.attempt_id == attempt.id)) is None:
        background.add_task(process_attempt, attempt.id)
    message = (
        "Time was up, so your saved answers were submitted automatically at the deadline."
        if attempt.auto_submitted
        else "Your answers have been submitted."
    ) + " Your results will appear in your history and be emailed to you once processing is complete."
    return SubmitResponse(
        attempt_id=attempt.id,
        status=attempt.status,
        submitted_at=attempt.submitted_at,
        auto_submitted=attempt.auto_submitted,
        already_submitted=not newly,
        message=message,
    )


@router.get("/attempts/{attempt_id}/status", response_model=AttemptStatus)
def attempt_status(attempt_id: int, user: PortalUser = Depends(get_portal_user), db: Session = Depends(get_db)):
    ctx = _attempt_context(db, user, attempt_id)
    attempt = attempt_service.get_owned_attempt(db, ctx, attempt_id)
    if attempt_service.finalize_if_expired(db, attempt):
        db.commit()
    return AttemptStatus(
        attempt_id=attempt.id,
        status=attempt.status,
        started_at=attempt.started_at,
        deadline_at=attempt.deadline_at,
        submitted_at=attempt.submitted_at,
        remaining_seconds=attempt_service.remaining_seconds(attempt),
        auto_submitted=attempt.auto_submitted,
        result_ready=db.scalar(select(Result.id).where(Result.attempt_id == attempt.id)) is not None,
    )


@router.post("/attempts/{attempt_id}/proctoring/events", status_code=201)
def proctoring_event(
    attempt_id: int, body: ProctoringEventIn, user: PortalUser = Depends(require_portal_csrf), db: Session = Depends(get_db)
):
    ctx = _attempt_context(db, user, attempt_id)
    attempt = attempt_service.get_owned_attempt(db, ctx, attempt_id)
    event = proctoring_service.record_event(db, attempt, body.event_type, body.duration_seconds, body.details, body.snapshot)
    return {"id": event.id, "event_type": event.event_type}


# ---------------------------------------------------------------------------
# History and results
# ---------------------------------------------------------------------------
@router.get("/history")
def history(user: PortalUser = Depends(get_portal_user), db: Session = Depends(get_db)):
    return portal_service.history_for(db, user.candidate)


@router.get("/results/{result_id}")
def my_result(result_id: int, user: PortalUser = Depends(get_portal_user), db: Session = Depends(get_db)):
    return portal_service.result_for(db, user.candidate, result_id)
