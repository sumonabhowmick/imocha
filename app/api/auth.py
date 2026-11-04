from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import ADMIN_COOKIE, ADMIN_CSRF_COOKIE, _bearer_token, client_ip, get_current_admin
from app.models import User
from app.schemas.auth import AdminOut, LoginRequest, LoginResponse
from app.schemas.common import Message
from app.security import generate_token
from app.errors import AppError
from app.services import audit_service, auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Token login for API clients and scripts (returns a bearer token)."""
    try:
        user, token, expires_at = auth_service.authenticate_admin(db, body.email, body.password, client_ip(request))
    except AppError as exc:
        audit_service.record("admin.login_failed", actor_type="anonymous", actor_label=str(body.email), details={"reason": exc.code}, ip=client_ip(request))
        raise
    audit_service.admin(user, "admin.login", details={"method": "token"}, ip=client_ip(request))
    return LoginResponse(access_token=token, expires_at=expires_at, admin=AdminOut.model_validate(user))


@router.post("/session")
def session_login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Browser login for the admin web app.

    The session token goes into an HttpOnly cookie (page scripts can never read it); a
    separate CSRF token goes into a readable cookie that the app echoes in the
    X-CSRF-Token header. A new session is created on every login (no session fixation).
    """
    csrf_token = generate_token()
    try:
        user, token, expires_at = auth_service.authenticate_admin(
            db, body.email, body.password, client_ip(request), csrf_token=csrf_token
        )
    except AppError as exc:
        audit_service.record("admin.login_failed", actor_type="anonymous", actor_label=str(body.email), details={"reason": exc.code}, ip=client_ip(request))
        raise
    audit_service.admin(user, "admin.login", ip=client_ip(request))
    cookie = dict(secure=get_settings().is_production, samesite="strict", path="/", expires=expires_at)
    response.set_cookie(ADMIN_COOKIE, token, httponly=True, **cookie)
    response.set_cookie(ADMIN_CSRF_COOKIE, csrf_token, httponly=False, **cookie)
    return {"admin": AdminOut.model_validate(user).model_dump(mode="json"), "expires_at": expires_at}


@router.post("/logout", response_model=Message)
def logout(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    auth_service.logout(db, _bearer_token(authorization) or request.cookies.get(ADMIN_COOKIE), "admin")
    response.delete_cookie(ADMIN_COOKIE, path="/")
    response.delete_cookie(ADMIN_CSRF_COOKIE, path="/")
    return Message(message="Logged out.")


@router.get("/me", response_model=AdminOut)
def me(admin: User = Depends(get_current_admin)):
    return admin
