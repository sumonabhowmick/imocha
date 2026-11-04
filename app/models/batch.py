from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin, utcnow


class Batch(TimestampMixin, Base):
    """A named group of candidates (e.g. 'CSE 2026 - Section A'). Assigning an
    assessment to a batch assigns it to every active member."""

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    members = relationship("BatchMember", back_populates="batch", cascade="all, delete-orphan")


class BatchMember(Base):
    __tablename__ = "batch_members"
    __table_args__ = (UniqueConstraint("batch_id", "candidate_id", name="uq_batch_members_batch_candidate"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), nullable=False, index=True)
    # Deleting a candidate removes their memberships; batches themselves are kept.
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now())

    batch = relationship("Batch", back_populates="members")
    candidate = relationship("Candidate")
