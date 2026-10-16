"""Invitation endpoints.

The invitation token travels in the JSON body (not the URL path) so it never appears
in access logs. Responses never contain passcodes or tokens.
"""
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import CSRF_COOKIE, STUDENT_COOKIE, client_ip, get_current_admin
from app.schemas.candidate import InviteResponse
from app.schemas.common import Message
from app.schemas.student import ResendCodeRequest, VerifyRequest, VerifyResponse
from app.services import auth_service, invitation_service

router = APIRouter(prefix="/api", tags=["invitations"])


@router.post("/assignments/{assignment_id}/invite", response_model=InviteResponse, dependencies=[Depends(get_current_admin)])
def invite(assignment_id: int, db: Session = Depends(get_db)):
    """Send (or re-send) an invitation. Any previous link and code stop working."""
    issued = invitation_service.issue_invitation(db, assignment_id)
    log = issued.email_log
    if log.status == "sent":
        message = "Invitation email sent."
    elif log.status == "outbox":
        message = "Invitation saved to the development outbox folder (EMAIL_BACKEND=outbox); no real email was sent."
    else:
        message = f"Invitation created but the email failed: {log.last_error}. You can retry from Email Status."
    return InviteResponse(assignment_id=assignment_id, email_status=log.status, email_log_id=log.id, message=message)


@router.post("/invitations/verify", response_model=VerifyResponse)
def verify(body: VerifyRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    session_token, csrf_token = invitation_service.verify_passcode(db, body.token, body.passcode, client_ip(request))
    cookie_args = dict(secure=settings.is_production, samesite="strict", path="/")
    # HttpOnly: page scripts cannot read the session cookie.
    response.set_cookie(STUDENT_COOKIE, session_token, httponly=True, **cookie_args)
    # Readable by the page so it can echo it in the X-CSRF-Token header.
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, **cookie_args)
    return VerifyResponse(message="Verified.", csrf_token=csrf_token)


@router.post("/invitations/resend-code", response_model=Message)
def resend_code(body: ResendCodeRequest, request: Request, db: Session = Depends(get_db)):
    return Message(message=invitation_service.resend_code(db, body.token, client_ip(request)))


@router.post("/student/logout", response_model=Message)
def student_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    auth_service.logout(db, request.cookies.get(STUDENT_COOKIE), "student")
    response.delete_cookie(STUDENT_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return Message(message="Signed out.")
