from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin


class EmailLog(TimestampMixin, Base):
    """Delivery record for every email. Never contains passcodes, tokens or message bodies."""

    __tablename__ = "email_logs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'sent', 'failed', 'outbox')", name="ck_email_logs_status"),
        # Exactly one result email per result: retries update this row instead of adding new ones.
        Index(
            "uq_email_logs_one_result_email",
            "related_result_id",
            unique=True,
            postgresql_where=text("notification_type = 'result'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    # invitation | passcode | result | admin_result_ready | admin_feedback_failed
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    related_assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_assignments.id", ondelete="SET NULL"), index=True
    )
    related_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("results.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
