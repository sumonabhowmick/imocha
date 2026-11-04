from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin


class BankQuestion(TimestampMixin, Base):
    """A reusable question in the question bank.

    Adding a bank question to an assessment COPIES it into that assessment's questions,
    so later edits to the bank never change a published assessment or past results.
    """

    __tablename__ = "bank_questions"
    __table_args__ = (
        CheckConstraint(
            "question_type IN ('mcq_single', 'mcq_multi', 'text', 'coding')", name="ck_bank_questions_type"
        ),
        CheckConstraint("difficulty IN ('beginner', 'intermediate', 'advanced')", name="ck_bank_questions_difficulty"),
        CheckConstraint("points >= 0", name="ck_bank_questions_points"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(String(20), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False, default="", index=True)
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False, default="intermediate")
    points: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=Decimal("1"))
    options_json: Mapped[list | None] = mapped_column(JSONB)
    correct_answer: Mapped[object | None] = mapped_column(JSONB)
    grading_rubric: Mapped[str] = mapped_column(Text, nullable=False, default="")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
