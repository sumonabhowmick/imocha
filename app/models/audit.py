from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, utcnow


class AuditEvent(Base):
    """Append-only record of security-relevant actions (who did what, when, from where).
    Never contains passwords, passcodes, tokens or snapshots."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("actor_type IN ('admin', 'candidate', 'agent', 'system', 'anonymous')", name="ck_audit_actor_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now(), index=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    actor_label: Mapped[str] = mapped_column(String(254), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(64))
