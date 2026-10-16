from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utcnow


class AssessmentAssignment(Base):
    __tablename__ = "assessment_assignments"
    __table_args__ = (
        UniqueConstraint("assessment_id", "candidate_id", name="uq_assignment_assessment_candidate"),
        CheckConstraint(
            "status IN ('assigned', 'invited', 'in_progress', 'completed', 'revoked')",
            name="ck_assignments_status",
        ),
        CheckConstraint("max_attempts >= 1", name="ck_assignments_max_attempts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # RESTRICT: assessments/candidates with assignments cannot be deleted (history is kept).
    assessment_id: Mapped[int] = mapped_column(
        ForeignKey("assessments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="assigned")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    assessment = relationship("Assessment")
    candidate = relationship("Candidate")
    attempts = relationship("Attempt", back_populates="assignment", order_by="Attempt.attempt_number")
