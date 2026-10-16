from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class Attempt(TimestampMixin, Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("assignment_id", "attempt_number", name="uq_attempts_assignment_number"),
        CheckConstraint("status IN ('in_progress', 'submitted')", name="ck_attempts_status"),
        CheckConstraint("deadline_at > started_at", name="ck_attempts_deadline_after_start"),
        # Only one attempt may be in progress per assignment (prevents duplicate starts).
        Index(
            "uq_attempts_one_in_progress",
            "assignment_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_assignments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="in_progress")
    auto_submitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    assignment = relationship("AssessmentAssignment", back_populates="attempts")
    answers = relationship("Answer", back_populates="attempt", cascade="all, delete-orphan")
    result = relationship("Result", back_populates="attempt", uselist=False)
