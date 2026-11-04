from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
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
        CheckConstraint("proctoring_mode IN ('off', 'optional', 'required')", name="ck_assessments_proctoring_mode"),
        CheckConstraint(
            "reminder_minutes_before_end IS NULL OR reminder_minutes_before_end > 0", name="ck_assessments_reminder"
        ),
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
    # Anti-cheating: each student gets their own question and/or option order.
    shuffle_questions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    shuffle_options: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Camera proctoring is mandatory: every assessment is "required" (no camera, no quiz).
    # Older values ("off", "optional") are kept valid only for historical rows.
    proctoring_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="required", server_default="required")
    # Send a reminder to students who have not started, this many minutes before the window closes.
    reminder_minutes_before_end: Mapped[int | None] = mapped_column(Integer)

    questions = relationship(
        "Question",
        back_populates="assessment",
        order_by="(Question.position, Question.id)",
        cascade="all, delete-orphan",
    )
    creator = relationship("User")
