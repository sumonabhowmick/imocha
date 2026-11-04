from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, utcnow

PROCTORING_EVENT_TYPES = (
    "consent_given",
    "camera_started",
    "camera_denied",
    "camera_stopped",
    "face_missing",
    "multiple_faces",
    "face_returned",
    "tab_hidden",
    "tab_visible",
    "fullscreen_exit",
    "fullscreen_enter",
)
FLAG_EVENT_TYPES = ("camera_denied", "camera_stopped", "face_missing", "multiple_faces", "tab_hidden", "fullscreen_exit")


class ProctoringEvent(Base):
    """Something the in-browser proctoring noticed during an attempt.

    Events are evidence for an ADMIN to review; they never change a score automatically.
    A small snapshot is stored only for flagged events (and only after consent).
    """

    __tablename__ = "proctoring_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN (" + ", ".join(f"'{t}'" for t in PROCTORING_EVENT_TYPES) + ")",
            name="ck_proctoring_events_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now())
    duration_seconds: Mapped[int | None] = mapped_column(Integer)  # e.g. how long the face was missing
    details: Mapped[dict | None] = mapped_column(JSONB)
    snapshot: Mapped[bytes | None] = mapped_column(LargeBinary)  # small JPEG, flagged events only
    snapshot_mime: Mapped[str | None] = mapped_column(String(20))
