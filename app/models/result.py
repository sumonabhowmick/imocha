from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class Result(TimestampMixin, Base):
    """The official, verified score of an attempt. One per attempt (unique attempt_id)."""

    __tablename__ = "results"
    __table_args__ = (
        CheckConstraint("result_status IN ('final', 'provisional')", name="ck_results_status"),
        CheckConstraint("raw_score >= 0 AND raw_score <= max_score", name="ck_results_score_bounds"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("attempts.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    raw_score: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    max_score: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    category: Mapped[str | None] = mapped_column(String(50))
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incorrect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unanswered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # "objective", "ai_assisted", "manual" or "mixed"
    grading_method: Mapped[str] = mapped_column(String(20), nullable=False, default="objective")
    # final = every answer graded; provisional = some answers await admin review.
    result_status: Mapped[str] = mapped_column(String(20), nullable=False, default="final")
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    attempt = relationship("Attempt", back_populates="result")
    feedback = relationship("Feedback", back_populates="result")
