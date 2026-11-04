"""Browser-based proctoring: camera face checks, tab switches and fullscreen exits.

Detection runs in the student's browser (no video leaves the device). The server only
receives small event records and, for flagged events after consent, one small JPEG
snapshot. Events are evidence for an ADMIN to review: they never change a score and
are never used by the AI to judge anyone.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import utcnow
from app.errors import AppError, bad_request, not_found
from app.models import Attempt, ProctoringEvent
from app.models.proctoring import FLAG_EVENT_TYPES, PROCTORING_EVENT_TYPES
from app.security import rate_limiter

logger = logging.getLogger(__name__)

MAX_SNAPSHOT_BYTES = 150_000
MAX_DETAILS_CHARS = 1000


def record_consent(db: Session, attempt: Attempt) -> None:
    if attempt.proctoring_consent_at is None:
        attempt.proctoring_consent_at = utcnow()
        db.add(ProctoringEvent(attempt_id=attempt.id, event_type="consent_given"))
        db.commit()


def _decode_snapshot(data: str) -> bytes:
    if data.startswith("data:"):
        header, _, data = data.partition(",")
        if "image/jpeg" not in header:
            raise bad_request("Snapshots must be JPEG images.")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise bad_request("The snapshot is not valid base64.") from exc
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise bad_request("The snapshot is too large.")
    if not raw.startswith(b"\xff\xd8\xff"):
        raise bad_request("Snapshots must be JPEG images.")
    return raw


def record_event(
    db: Session,
    attempt: Attempt,
    event_type: str,
    duration_seconds: int | None = None,
    details: dict | None = None,
    snapshot: str | None = None,
) -> ProctoringEvent:
    if event_type not in PROCTORING_EVENT_TYPES or event_type == "consent_given":
        raise bad_request("Unknown proctoring event.")
    if attempt.status != "in_progress":
        raise AppError(409, "This attempt is no longer in progress.", "attempt_closed")
    if not rate_limiter.hit(f"proctoring:{attempt.id}", 240, 600):
        raise AppError(429, "Too many proctoring events.", "rate_limited")
    if details is not None and len(json.dumps(details, default=str)) > MAX_DETAILS_CHARS:
        raise bad_request("Event details are too large.")
    image = None
    if snapshot:
        if event_type not in FLAG_EVENT_TYPES:
            raise bad_request("Snapshots are only accepted for flagged events.")
        if attempt.proctoring_consent_at is None:
            raise AppError(403, "Camera consent has not been given for this attempt.", "no_consent")
        image = _decode_snapshot(snapshot)
    event = ProctoringEvent(
        attempt_id=attempt.id,
        event_type=event_type,
        duration_seconds=max(0, min(duration_seconds, 86_400)) if duration_seconds is not None else None,
        details=details,
        snapshot=image,
        snapshot_mime="image/jpeg" if image else None,
    )
    db.add(event)
    db.commit()
    return event


# ---------------------------------------------------------------------------
# Admin review
# ---------------------------------------------------------------------------
def flag_counts(db: Session, attempt_ids: list[int]) -> dict[int, int]:
    if not attempt_ids:
        return {}
    return dict(
        db.execute(
            select(ProctoringEvent.attempt_id, func.count())
            .where(ProctoringEvent.attempt_id.in_(attempt_ids), ProctoringEvent.event_type.in_(FLAG_EVENT_TYPES))
            .group_by(ProctoringEvent.attempt_id)
        ).all()
    )


def timeline(db: Session, attempt_id: int) -> dict:
    attempt = db.get(Attempt, attempt_id)
    if attempt is None:
        raise not_found("Attempt")
    events = list(
        db.scalars(select(ProctoringEvent).where(ProctoringEvent.attempt_id == attempt_id).order_by(ProctoringEvent.occurred_at, ProctoringEvent.id))
    )
    counts: dict[str, int] = {}
    for e in events:
        counts[e.event_type] = counts.get(e.event_type, 0) + 1
    return {
        "attempt_id": attempt_id,
        "consent_at": attempt.proctoring_consent_at,
        "started_at": attempt.started_at,
        "submitted_at": attempt.submitted_at,
        "flag_count": sum(n for t, n in counts.items() if t in FLAG_EVENT_TYPES),
        # The browser reports how long the face was gone when it comes back (face_returned).
        "face_missing_seconds": sum(e.duration_seconds or 0 for e in events if e.event_type in ("face_missing", "face_returned")),
        "counts": counts,
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "flag": e.event_type in FLAG_EVENT_TYPES,
                "occurred_at": e.occurred_at,
                "duration_seconds": e.duration_seconds,
                "details": e.details,
                "has_snapshot": e.snapshot is not None,
            }
            for e in events
        ],
        "note": "Flags are signals for human review, not proof of misconduct. Scores are never changed automatically.",
    }


def snapshot(db: Session, event_id: int) -> tuple[bytes, str]:
    event = db.get(ProctoringEvent, event_id)
    if event is None or event.snapshot is None:
        raise not_found("Snapshot")
    return event.snapshot, event.snapshot_mime or "image/jpeg"


def purge_old_snapshots(db: Session) -> int:
    """Delete snapshot images older than the retention period (events are kept)."""
    days = get_settings().proctoring_snapshot_retention_days
    cutoff = utcnow() - timedelta(days=days)
    res = db.execute(
        update(ProctoringEvent)
        .where(ProctoringEvent.snapshot.is_not(None), ProctoringEvent.occurred_at < cutoff)
        .values(snapshot=None, snapshot_mime=None)
    )
    db.commit()
    return res.rowcount or 0
