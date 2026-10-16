from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class Feedback(TimestampMixin, Base):
    """AI-generated feedback. Student and admin reports are separate rows (per result);
    group reports belong to an assessment."""

    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint("feedback_type IN ('student', 'admin', 'group')", name="ck_feedback_type"),
        CheckConstraint(
            "generation_status IN ('pending', 'completed', 'failed')", name="ck_feedback_status"
        ),
        CheckConstraint(
            "(feedback_type = 'group' AND assessment_id IS NOT NULL AND result_id IS NULL) OR "
            "(feedback_type <> 'group' AND result_id IS NOT NULL)",
            name="ck_feedback_owner",
        ),
        Index(
            "uq_feedback_result_type",
            "result_id",
            "feedback_type",
            unique=True,
            postgresql_where=text("result_id IS NOT NULL"),
        ),
        Index(
            "uq_feedback_group_assessment",
            "assessment_id",
            unique=True,
            postgresql_where=text("feedback_type = 'group'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    result_id: Mapped[int | None] = mapped_column(ForeignKey("results.id", ondelete="CASCADE"), index=True)
    assessment_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    feedback_type: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[dict | None] = mapped_column(JSONB)
    generation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    model_name: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    result = relationship("Result", back_populates="feedback")
