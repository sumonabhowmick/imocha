from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class Answer(TimestampMixin, Base):
    """One current answer per question per attempt (enforced by a unique constraint)."""

    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_id", name="uq_answers_attempt_question"),
        CheckConstraint(
            "grading_method IS NULL OR grading_method IN ('objective', 'ai_assisted', 'manual', 'pending_review')",
            name="ck_answers_grading_method",
        ),
        CheckConstraint(
            "awarded_points IS NULL OR (awarded_points >= 0 AND awarded_points <= max_points)",
            name="ck_answers_points_in_bounds",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # RESTRICT: a question that has been answered can never be deleted.
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # {"selected": "A"} / {"selected": ["A", "C"]} / {"text": "..."}; NULL = unanswered.
    answer_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    awarded_points: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    # Snapshot of the question's points at grading time.
    max_points: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal("0"))
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    grading_method: Mapped[str | None] = mapped_column(String(20))
    grading_rationale: Mapped[str | None] = mapped_column(Text)
    # Model used for AI-assisted grading (NULL for objective or manual grading).
    grading_model: Mapped[str | None] = mapped_column(String(100))
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attempt = relationship("Attempt", back_populates="answers")
    question = relationship("Question")
