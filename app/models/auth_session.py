from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class AuthSession(Base):
    """Server-side session for an admin (bearer token) or a student (cookie).

    Only a SHA-256 hash of the session token is stored, so a database leak does not
    reveal usable session tokens. Server-side storage makes logout and expiry real.
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("kind IN ('admin', 'student')", name="ck_auth_sessions_kind"),
        CheckConstraint(
            "(kind = 'admin' AND user_id IS NOT NULL) OR (kind = 'student' AND invitation_id IS NOT NULL)",
            name="ck_auth_sessions_owner",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    invitation_id: Mapped[int | None] = mapped_column(
        ForeignKey("invitations.id", ondelete="CASCADE"), index=True
    )
    csrf_token_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

    user = relationship("User")
    invitation = relationship("Invitation")
