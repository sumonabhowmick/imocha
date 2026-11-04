"""Reminder emails for students who have not started an assessment yet.

Run by the background sweeper. A reminder goes out once per assignment (enforced by a
unique index on email_logs), when the access window is about to close. It contains no
codes or links with secrets: students use their invitation email or the student portal.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import utcnow
from app.models import Assessment, AssessmentAssignment, Attempt, Candidate, EmailLog
from app.services import email_service

logger = logging.getLogger(__name__)


def due_assignments(db: Session) -> list[AssessmentAssignment]:
    now = utcnow()
    candidates = db.scalars(
        select(AssessmentAssignment)
        .join(Assessment, Assessment.id == AssessmentAssignment.assessment_id)
        .join(Candidate, Candidate.id == AssessmentAssignment.candidate_id)
        .where(
            Assessment.status == "published",
            Assessment.reminder_minutes_before_end.is_not(None),
            Assessment.access_end_at > now,
            Assessment.access_start_at <= now,
            AssessmentAssignment.status.in_(("assigned", "invited")),
            Candidate.is_active.is_(True),
            ~exists().where(Attempt.assignment_id == AssessmentAssignment.id),
            ~exists().where(
                EmailLog.related_assignment_id == AssessmentAssignment.id, EmailLog.notification_type == "reminder"
            ),
        )
    )
    return [
        a
        for a in candidates
        if now >= a.assessment.access_end_at - timedelta(minutes=a.assessment.reminder_minutes_before_end)
    ]


def send_due_reminders(db: Session) -> int:
    sent = 0
    for assignment in due_assignments(db):
        assessment, candidate = assignment.assessment, assignment.candidate
        subject = f"Reminder: {assessment.title} closes soon"
        html, text = email_service._render(
            "reminder",
            candidate_name=candidate.name,
            assessment_title=assessment.title,
            window_end=email_service.format_local(assessment.access_end_at),
            duration_minutes=assessment.duration_minutes,
            portal_url=get_settings().public_base_url.rstrip("/") + "/portal",
        )
        try:
            log = email_service.new_log(db, candidate.email, "reminder", subject, assignment_id=assignment.id)
        except IntegrityError:  # another worker already queued this reminder
            db.rollback()
            continue
        email_service.deliver(db, log, html, text)
        sent += 1
    if sent:
        logger.info("Sent %s reminder email(s)", sent)
    return sent
