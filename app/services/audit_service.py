"""Security audit trail: who did what, when, from where.

Writes are best-effort and use their own session, so a failure to audit never breaks
(or rolls back) the action being audited. Details must never contain passwords,
passcodes, tokens or images.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AuditEvent, User

logger = logging.getLogger("assessflow.audit")


def record(
    action: str,
    *,
    actor_type: str = "system",
    actor_id: int | None = None,
    actor_label: str = "",
    target_type: str | None = None,
    target_id: int | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> None:
    try:
        with SessionLocal() as db:
            db.add(
                AuditEvent(
                    action=action[:60],
                    actor_type=actor_type,
                    actor_id=actor_id,
                    actor_label=(actor_label or "")[:254],
                    target_type=target_type,
                    target_id=target_id,
                    details=details,
                    ip_address=(ip or None) and ip[:64],
                )
            )
            db.commit()
    except Exception:  # auditing must never break the request
        logger.exception("Could not write audit event %s", action)


def admin(user: User, action: str, *, target_type: str | None = None, target_id: int | None = None, details: dict | None = None, ip: str | None = None) -> None:
    record(action, actor_type="admin", actor_id=user.id, actor_label=user.email, target_type=target_type, target_id=target_id, details=details, ip=ip)


def list_events(
    db: Session,
    action: str | None = None,
    actor_type: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    stmt = select(AuditEvent)
    count = select(func.count(AuditEvent.id))
    if action:
        stmt, count = stmt.where(AuditEvent.action == action), count.where(AuditEvent.action == action)
    if actor_type:
        stmt, count = stmt.where(AuditEvent.actor_type == actor_type), count.where(AuditEvent.actor_type == actor_type)
    if search:
        like = f"%{search.strip()}%"
        cond = AuditEvent.actor_label.ilike(like) | AuditEvent.action.ilike(like)
        stmt, count = stmt.where(cond), count.where(cond)
    rows = db.scalars(stmt.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(limit).offset(offset))
    return {
        "total": db.scalar(count) or 0,
        "items": [
            {
                "id": e.id,
                "occurred_at": e.occurred_at,
                "actor_type": e.actor_type,
                "actor_label": e.actor_label,
                "action": e.action,
                "target_type": e.target_type,
                "target_id": e.target_id,
                "details": e.details,
                "ip_address": e.ip_address,
            }
            for e in rows
        ],
    }
