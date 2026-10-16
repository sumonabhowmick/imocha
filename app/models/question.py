from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

QUESTION_TYPES = ("mcq_single", "mcq_multi", "text", "coding")
OBJECTIVE_TYPES = ("mcq_single", "mcq_multi")


class Question(TimestampMixin, Base):
    """A question. `correct_answer` and `grading_rubric` are private grading data and are
    never included in student-facing schemas."""

    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint(
            "question_type IN ('mcq_single', 'mcq_multi', 'text', 'coding')", name="ck_questions_type"
        ),
        CheckConstraint("points >= 0", name="ck_questions_points_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(String(20), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    points: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal("1"))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # [{"id": "A", "text": "..."}, ...] for multiple-choice questions.
    options_json: Mapped[list | None] = mapped_column(JSONB)
    # mcq_single: "A"; mcq_multi: ["A", "C"]; text/coding: optional model answer string.
    correct_answer: Mapped[object | None] = mapped_column(JSONB)
    grading_rubric: Mapped[str] = mapped_column(Text, nullable=False, default="")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")

    assessment = relationship("Assessment", back_populates="questions")
