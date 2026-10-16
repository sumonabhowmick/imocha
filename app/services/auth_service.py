"""Admin authentication and server-side sessions (admin and student)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import utcnow
from app.errors import AppError
from app.models import AuthSession, User
from app.security import (
    generate_token,
    hash_password,
    hash_session_token,
    rate_limiter,
    verify_password,
)

logger = logging.getLogger(__name__)

GENERIC_LOGIN_ERROR = "Invalid email or password."
MIN_PASSWORD_LENGTH = 12


def normalize_email(email: str) -> str:
    return email.strip().lower()


# ---------------------------------------------------------------------------
# Admin accounts
# ---------------------------------------------------------------------------
def validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")
    if password.isdigit() or password.isalpha():
        raise ValueError("Password must mix letters with digits or symbols.")


def create_admin(db: Session, name: str, email: str, password: str) -> User:
    validate_password_strength(password)
    email = normalize_email(email)
    if db.scalar(select(User).where(User.email == email)):
        raise ValueError("An admin with this email already exists.")
    user = User(name=name.strip(), email=email, password_hash=hash_password(password), role="admin")
    db.add(user)
    db.commit()
    logger.info("Admin account created (id=%s)", user.id)
    return user


def authenticate_admin(db: Session, email: str, password: str, client_ip: str) -> tuple[User, str, datetime]:
    """Verify credentials and open a new session. Returns (user, raw_token, expires_at).

    Errors are deliberately generic so they do not reveal whether an account exists.
    """
    settings = get_settings()
    email = normalize_email(email)
    allowed_ip = rate_limiter.hit(f"login-ip:{client_ip}", settings.login_max_attempts * 4, settings.login_window_seconds)
    allowed_email = rate_limiter.hit(f"login-email:{email}", settings.login_max_attempts, settings.login_window_seconds)
    if not (allowed_ip and allowed_email):
        raise AppError(429, "Too many login attempts. Please wait a few minutes and try again.", "rate_limited")

    user = db.scalar(select(User).where(User.email == email))
    password_ok = verify_password(user.password_hash if user else None, password)
    if not user or not password_ok or not user.is_active or user.role != "admin":
        logger.info("Failed admin login attempt")
        raise AppError(401, GENERIC_LOGIN_ERROR, "invalid_credentials")

    rate_limiter.reset(f"login-email:{email}")
    user.last_login_at = utcnow()
    token, expires_at = _create_session(db, kind="admin", user_id=user.id)
    db.commit()
    return user, token, expires_at


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def _create_session(
    db: Session,
    *,
    kind: str,
    user_id: int | None = None,
    invitation_id: int | None = None,
    csrf_token: str | None = None,
    ttl_minutes: int | None = None,
) -> tuple[str, datetime]:
    """Always issues a brand-new random token (prevents session fixation: a client can
    never choose or keep its own session identifier across a login)."""
    settings = get_settings()
    ttl = ttl_minutes or (
        settings.admin_session_ttl_minutes if kind == "admin" else settings.student_session_ttl_minutes
    )
    token = generate_token()
    expires_at = utcnow() + timedelta(minutes=ttl)
    db.add(
        AuthSession(
            token_hash=hash_session_token(token),
            kind=kind,
            user_id=user_id,
            invitation_id=invitation_id,
            csrf_token_hash=hash_session_token(csrf_token) if csrf_token else None,
            expires_at=expires_at,
        )
    )
    return token, expires_at


def create_student_session(db: Session, invitation_id: int) -> tuple[str, str]:
    """Revoke earlier sessions for this invitation and open a new one.
    Returns (session_token, csrf_token). The caller commits."""
    revoke_student_sessions(db, invitation_id)
    csrf_token = generate_token()
    token, _ = _create_session(db, kind="student", invitation_id=invitation_id, csrf_token=csrf_token)
    return token, csrf_token


def revoke_student_sessions(db: Session, invitation_id: int) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.invitation_id == invitation_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


def get_active_session(db: Session, raw_token: str | None, kind: str) -> AuthSession | None:
    if not raw_token or len(raw_token) > 200:
        return None
    session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_session_token(raw_token))
    )
    if not session or session.kind != kind or session.revoked_at is not None:
        return None
    if session.expires_at <= utcnow():
        return None
    return session


def get_admin_for_token(db: Session, raw_token: str | None) -> User | None:
    session = get_active_session(db, raw_token, "admin")
    if not session:
        return None
    user = db.get(User, session.user_id)
    if not user or not user.is_active or user.role != "admin":
        return None
    return user


def logout(db: Session, raw_token: str | None, kind: str) -> None:
    session = get_active_session(db, raw_token, kind)
    if session:
        session.revoked_at = utcnow()
        db.commit()


def extend_session(session: AuthSession, until: datetime) -> None:
    """Make sure a student session lasts at least until `until` (e.g. the attempt deadline)."""
    if session.expires_at < until:
        session.expires_at = until
