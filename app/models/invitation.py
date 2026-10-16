from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class Invitation(TimestampMixin, Base):
    """Email invitation. Only keyed hashes of the token and passcode are stored."""

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'verified', 'revoked', 'expired')", name="ck_invitations_status"
        ),
        # At most one usable invitation per assignment.
        Index(
            "uq_invitations_one_usable_per_assignment",
            "assignment_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'verified')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invitation_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    passcode_hash: Mapped[str | None] = mapped_column(String(64))
    passcode_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    passcode_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invitation_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    resend_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_code_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assignment = relationship("AssessmentAssignment")
