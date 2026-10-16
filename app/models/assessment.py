from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

COMPLETION_POLICIES = ("finish_after_window", "hard_end_at_window")
DIFFICULTIES = ("beginner", "intermediate", "advanced")


class Assessment(TimestampMixin, Base):
    """A quiz. Only draft/published/archived are stored; scheduled/active/completed are
    derived from the access window (see assessment_service.display_status)."""

    __tablename__ = "assessments"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_assessments_status"),
        CheckConstraint(
            "completion_policy IN ('finish_after_window', 'hard_end_at_window')",
            name="ck_assessments_completion_policy",
        ),
        CheckConstraint("duration_minutes > 0", name="ck_assessments_duration_positive"),
        CheckConstraint("max_attempts >= 1", name="ck_assessments_max_attempts"),
        CheckConstraint(
            "access_start_at IS NULL OR access_end_at IS NULL OR access_end_at > access_start_at",
            name="ck_assessments_window_order",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False, default="intermediate")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    access_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_policy: Mapped[str] = mapped_column(String(30), nullable=False, default="finish_after_window")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    # Optional assessment-specific performance category scale; NULL = use the default scale.
    category_scale_json: Mapped[list | None] = mapped_column(JSONB)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    questions = relationship(
        "Question",
        back_populates="assessment",
        order_by="(Question.position, Question.id)",
        cascade="all, delete-orphan",
    )
    creator = relationship("User")
