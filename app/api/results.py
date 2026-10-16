from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin
from app.errors import conflict
from app.models import User
from app.services import email_service, grading_service, result_service
from app.workflows.assessment_workflow import after_manual_review

router = APIRouter(prefix="/api/admin", tags=["results"], dependencies=[Depends(get_current_admin)])


class ManualGradeRequest(BaseModel):
    awarded_points: Decimal = Field(ge=0, le=1000, decimal_places=2)
    rationale: str = Field(min_length=1, max_length=2000)


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    return result_service.overview(db)


@router.get("/results")
def list_results(
    assessment_id: int | None = None,
    candidate_id: int | None = None,
    result_status: str | None = Query(None, pattern="^(final|provisional)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return result_service.list_results(db, assessment_id, candidate_id, result_status, limit, offset)


@router.get("/results/{result_id}")
def result_detail(result_id: int, db: Session = Depends(get_db)):
    return result_service.result_detail(db, result_id)


@router.patch("/answers/{answer_id}/grade")
def grade_answer(
    answer_id: int,
    body: ManualGradeRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    result, became_final = grading_service.manual_grade(db, answer_id, body.awarded_points, body.rationale, admin)
    if became_final:
        background.add_task(after_manual_review, result.id)
    return {"result_id": result.id, "result_status": result.result_status, "became_final": became_final}


@router.get("/assessments/{assessment_id}/analytics")
def analytics(assessment_id: int, db: Session = Depends(get_db)):
    return result_service.assessment_analytics(db, assessment_id)


@router.post("/results/{result_id}/send-result-email")
def send_result_email(result_id: int, db: Session = Depends(get_db)):
    """Send the student's result email even if AI feedback is unavailable (score only)."""
    log = email_service.send_result_email(db, result_id, allow_without_feedback=True)
    if log is None:
        raise conflict("The result email was already delivered, or the result is still provisional.")
    return {"email_log_id": log.id, "status": log.status, "error": log.last_error}
