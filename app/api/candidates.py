from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin
from app.errors import AppError
from app.models import User
from app.schemas.candidate import (
    AssignmentOut,
    AssignRequest,
    CandidateCreate,
    CandidateOut,
    CandidateUpdate,
)
from app.services import candidate_service, invitation_service

router = APIRouter(prefix="/api", tags=["candidates"], dependencies=[Depends(get_current_admin)])


@router.post("/candidates", response_model=CandidateOut, status_code=201)
def create_candidate(body: CandidateCreate, db: Session = Depends(get_db)):
    return candidate_service.create_candidate(db, body)


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(
    search: str | None = Query(None, max_length=100),
    active_only: bool = False,
    db: Session = Depends(get_db),
):
    return candidate_service.list_candidates(db, search, active_only)


@router.patch("/candidates/{candidate_id}", response_model=CandidateOut)
def update_candidate(candidate_id: int, body: CandidateUpdate, db: Session = Depends(get_db)):
    return candidate_service.update_candidate(db, candidate_id, body)


@router.post("/assessments/{assessment_id}/assign")
def assign(
    assessment_id: int, body: AssignRequest, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)
):
    assignments = candidate_service.assign_candidates(db, assessment_id, body.candidate_ids, body.max_attempts, admin)
    invitations = []
    if body.send_invitations:
        for a in assignments:
            if a.status in ("assigned", "invited"):
                try:
                    issued = invitation_service.issue_invitation(db, a.id)
                    invitations.append({"assignment_id": a.id, "email_status": issued.email_log.status})
                except AppError as exc:
                    db.rollback()
                    invitations.append({"assignment_id": a.id, "error": exc.message})
    return {"assignment_ids": [a.id for a in assignments], "invitations": invitations}


@router.get("/assessments/{assessment_id}/assignments", response_model=list[AssignmentOut])
def assessment_assignments(assessment_id: int, db: Session = Depends(get_db)):
    return candidate_service.assignment_rows(db, assessment_id)


@router.get("/assignments", response_model=list[AssignmentOut])
def all_assignments(db: Session = Depends(get_db)):
    return candidate_service.assignment_rows(db)


@router.post("/assignments/{assignment_id}/revoke")
def revoke_assignment(assignment_id: int, db: Session = Depends(get_db)):
    assignment = candidate_service.revoke_assignment(db, assignment_id)
    return {"assignment_id": assignment.id, "status": assignment.status}
