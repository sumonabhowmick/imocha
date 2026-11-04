"""Batches and bulk candidate import (admin only)."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import client_ip, get_current_admin
from app.errors import AppError
from app.models import User
from app.services import audit_service, batch_service, invitation_service

router = APIRouter(prefix="/api", tags=["batches"], dependencies=[Depends(get_current_admin)])


class BatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=2000)


class BatchUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)


class MembersRequest(BaseModel):
    candidate_ids: list[int] = Field(min_length=1, max_length=2000)


class AssignBatchRequest(BaseModel):
    batch_id: int
    max_attempts: int | None = Field(None, ge=1, le=10)
    send_invitations: bool = False


class ImportRequest(BaseModel):
    csv_text: str = Field(min_length=1, max_length=1_000_000)
    batch_id: int | None = None
    dry_run: bool = True


@router.get("/batches")
def list_batches(db: Session = Depends(get_db)):
    return batch_service.list_batches(db)


@router.post("/batches", status_code=201)
def create_batch(body: BatchCreate, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    batch = batch_service.create_batch(db, body.name, body.description, admin)
    audit_service.admin(admin, "batch.create", target_type="batch", target_id=batch["id"], details={"name": batch["name"]}, ip=client_ip(request))
    return batch


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    return batch_service.batch_detail(db, batch_id)


@router.patch("/batches/{batch_id}")
def update_batch(batch_id: int, body: BatchUpdate, db: Session = Depends(get_db)):
    return batch_service.update_batch(db, batch_id, body.name, body.description)


@router.delete("/batches/{batch_id}", status_code=204)
def delete_batch(batch_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    batch_service.delete_batch(db, batch_id)
    audit_service.admin(admin, "batch.delete", target_type="batch", target_id=batch_id, ip=client_ip(request))


@router.post("/batches/{batch_id}/members")
def add_members(batch_id: int, body: MembersRequest, db: Session = Depends(get_db)):
    return batch_service.add_members(db, batch_id, body.candidate_ids)


@router.delete("/batches/{batch_id}/members/{candidate_id}")
def remove_member(batch_id: int, candidate_id: int, db: Session = Depends(get_db)):
    return batch_service.remove_member(db, batch_id, candidate_id)


@router.post("/assessments/{assessment_id}/assign-batch")
def assign_batch(
    assessment_id: int,
    body: AssignBatchRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Assign an assessment to every active member of a batch, optionally inviting them."""
    assignments = batch_service.assign_batch(db, assessment_id, body.batch_id, body.max_attempts, admin)
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
    audit_service.admin(
        admin, "assessment.assign_batch", target_type="assessment", target_id=assessment_id,
        details={"batch_id": body.batch_id, "assigned": len(assignments), "invited": len(invitations)}, ip=client_ip(request),
    )
    return {"assignment_ids": [a.id for a in assignments], "invitations": invitations}


@router.post("/candidates/import")
def import_candidates(body: ImportRequest, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Bulk import candidates from CSV text (columns: name,email). Use dry_run=true to
    preview what would happen; nothing is saved in a dry run."""
    result = batch_service.import_candidates(db, body.csv_text, body.batch_id, body.dry_run)
    if not body.dry_run:
        audit_service.admin(admin, "candidates.import", details={**result["summary"], "batch_id": body.batch_id}, ip=client_ip(request))
    return result
