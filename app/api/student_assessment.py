"""Student assessment endpoints.

Identity and assignment come exclusively from the authenticated student session. An
attempt id in the URL is only accepted if it belongs to that session's assignment.
"""
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import StudentContext, get_student_context, require_student_csrf
from app.models import Result
from app.schemas.student import (
    AnswerIn,
    AnswerSaved,
    AttemptOut,
    AttemptQuestions,
    AttemptStatus,
    StudentAssessmentOverview,
    SubmitResponse,
)
from app.services import attempt_service
from app.workflows.assessment_workflow import process_attempt

router = APIRouter(prefix="/api/student", tags=["student"])


@router.get("/assessment", response_model=StudentAssessmentOverview)
def assessment_overview(
    background: BackgroundTasks, ctx: StudentContext = Depends(get_student_context), db: Session = Depends(get_db)
):
    data, finalized = attempt_service.overview(db, ctx)
    for attempt_id in finalized:
        background.add_task(process_attempt, attempt_id)
    return data


@router.post("/attempts", response_model=AttemptOut)
def start_attempt(ctx: StudentContext = Depends(require_student_csrf), db: Session = Depends(get_db)):
    attempt, _created = attempt_service.start_attempt(db, ctx)
    return AttemptOut(
        attempt_id=attempt.id,
        attempt_number=attempt.attempt_number,
        status=attempt.status,
        started_at=attempt.started_at,
        deadline_at=attempt.deadline_at,
        remaining_seconds=attempt_service.remaining_seconds(attempt),
    )


@router.get("/attempts/{attempt_id}/questions", response_model=AttemptQuestions)
def attempt_questions(attempt_id: int, ctx: StudentContext = Depends(get_student_context), db: Session = Depends(get_db)):
    return attempt_service.questions_for_attempt(db, ctx, attempt_id)


@router.put("/attempts/{attempt_id}/answers/{question_id}", response_model=AnswerSaved)
def save_answer(
    attempt_id: int,
    question_id: int,
    body: AnswerIn,
    ctx: StudentContext = Depends(require_student_csrf),
    db: Session = Depends(get_db),
):
    attempt = attempt_service.save_answer(db, ctx, attempt_id, question_id, body.selected, body.text)
    return AnswerSaved(question_id=question_id, saved=True, remaining_seconds=attempt_service.remaining_seconds(attempt))


@router.post("/attempts/{attempt_id}/submit", response_model=SubmitResponse)
def submit(
    attempt_id: int,
    background: BackgroundTasks,
    ctx: StudentContext = Depends(require_student_csrf),
    db: Session = Depends(get_db),
):
    attempt, newly = attempt_service.submit_attempt(db, ctx, attempt_id)
    has_result = db.scalar(select(Result.id).where(Result.attempt_id == attempt.id)) is not None
    if not has_result:
        background.add_task(process_attempt, attempt.id)  # idempotent pipeline
    if attempt.auto_submitted:
        message = "Time was up, so your saved answers were submitted automatically at the deadline."
    else:
        message = "Your answers have been submitted."
    message += " Your results and feedback will be emailed to you once processing is complete."
    return SubmitResponse(
        attempt_id=attempt.id,
        status=attempt.status,
        submitted_at=attempt.submitted_at,
        auto_submitted=attempt.auto_submitted,
        already_submitted=not newly,
        message=message,
    )


@router.get("/attempts/{attempt_id}/status", response_model=AttemptStatus)
def attempt_status(attempt_id: int, ctx: StudentContext = Depends(get_student_context), db: Session = Depends(get_db)):
    attempt = attempt_service.get_owned_attempt(db, ctx, attempt_id)
    if attempt_service.finalize_if_expired(db, attempt):
        db.commit()
    return AttemptStatus(
        attempt_id=attempt.id,
        status=attempt.status,
        started_at=attempt.started_at,
        deadline_at=attempt.deadline_at,
        submitted_at=attempt.submitted_at,
        remaining_seconds=attempt_service.remaining_seconds(attempt),
        auto_submitted=attempt.auto_submitted,
        result_ready=db.scalar(select(Result.id).where(Result.attempt_id == attempt.id)) is not None,
    )
