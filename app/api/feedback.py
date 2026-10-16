from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin
from app.models import EmailLog
from app.services import feedback_service
from app.workflows.assessment_workflow import deliver_feedback_and_email, retry_email

router = APIRouter(prefix="/api/admin", tags=["feedback"], dependencies=[Depends(get_current_admin)])


@router.get("/results/{result_id}/feedback")
def result_feedback(result_id: int, db: Session = Depends(get_db)):
    """Both reports for one result. Admin-only: students never receive the admin report."""
    return feedback_service.get_result_feedback(db, result_id)


@router.post("/results/{result_id}/retry-feedback")
def retry_feedback(result_id: int, force: bool = False, db: Session = Depends(get_db)):
    """Regenerate failed (or, with force=true, all) feedback, then send the result email
    if it has not been delivered yet. The verified score is never modified."""
    summary = deliver_feedback_and_email(db, result_id, force_feedback=force)
    return {"result_id": result_id, **summary, **feedback_service.get_result_feedback(db, result_id)}


@router.get("/assessments/{assessment_id}/group-feedback")
def get_group_feedback(assessment_id: int, db: Session = Depends(get_db)):
    return {"assessment_id": assessment_id, "group": feedback_service.get_group_feedback(db, assessment_id)}


@router.post("/assessments/{assessment_id}/group-feedback")
def generate_group_feedback(assessment_id: int, db: Session = Depends(get_db)):
    fb = feedback_service.generate_group_feedback(db, assessment_id)
    return {"assessment_id": assessment_id, "group": feedback_service.feedback_to_out(fb)}


@router.get("/email-logs")
def email_logs(
    status: str | None = Query(None, pattern="^(queued|sent|failed|outbox)$"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    stmt = select(EmailLog).order_by(EmailLog.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(EmailLog.status == status)
    return [
        {
            "id": log.id,
            "recipient_email": log.recipient_email,
            "notification_type": log.notification_type,
            "subject": log.subject,
            "status": log.status,
            "attempts": log.attempts,
            "last_error": log.last_error,
            "related_assignment_id": log.related_assignment_id,
            "related_result_id": log.related_result_id,
            "sent_at": log.sent_at,
            "created_at": log.created_at,
        }
        for log in db.scalars(stmt)
    ]


@router.post("/email-logs/{email_log_id}/retry")
def retry_email_log(email_log_id: int, db: Session = Depends(get_db)):
    log = retry_email(db, email_log_id)
    return {"email_log_id": log.id, "status": log.status, "error": log.last_error}
