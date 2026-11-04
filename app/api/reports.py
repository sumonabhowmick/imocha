"""Admin reports: proctoring review, result exports and the audit log."""
import re

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import client_ip, get_current_admin
from app.models import User
from app.services import audit_service, export_service, proctoring_service
from app.services.assessment_service import get_assessment

router = APIRouter(prefix="/api/admin", tags=["reports"], dependencies=[Depends(get_current_admin)])


@router.get("/attempts/{attempt_id}/proctoring")
def proctoring_timeline(attempt_id: int, db: Session = Depends(get_db)):
    """Proctoring events for one attempt (images are fetched separately)."""
    return proctoring_service.timeline(db, attempt_id)


@router.get("/proctoring/snapshots/{event_id}")
def proctoring_snapshot(event_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    image, mime = proctoring_service.snapshot(db, event_id)
    audit_service.admin(admin, "proctoring.snapshot_viewed", target_type="proctoring_event", target_id=event_id, ip=client_ip(request))
    return Response(content=image, media_type=mime, headers={"Cache-Control": "no-store", "Content-Disposition": "inline"})


@router.get("/assessments/{assessment_id}/export")
def export_results(
    assessment_id: int,
    request: Request,
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Download results for one assessment as CSV or Excel."""
    title = get_assessment(db, assessment_id).title
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:50] or "assessment"
    if format == "xlsx":
        content = export_service.export_xlsx(db, assessment_id)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        content = export_service.export_csv(db, assessment_id)
        media = "text/csv; charset=utf-8"
    audit_service.admin(admin, "results.export", target_type="assessment", target_id=assessment_id, details={"format": format}, ip=client_ip(request))
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{slug}-results.{format}"', "Cache-Control": "no-store"},
    )


@router.get("/audit")
def audit_log(
    action: str | None = Query(None, max_length=60),
    actor_type: str | None = Query(None, pattern="^(admin|candidate|agent|system|anonymous)$"),
    search: str | None = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return audit_service.list_events(db, action, actor_type, search, limit, offset)
