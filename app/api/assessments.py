from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin
from app.models import User
from app.schemas.assessment import AssessmentCreate, AssessmentDetail, AssessmentOut, AssessmentUpdate, GenerateQuestionsRequest
from app.services import assessment_service

router = APIRouter(prefix="/api/assessments", tags=["assessments"], dependencies=[Depends(get_current_admin)])


@router.post("", response_model=AssessmentDetail, status_code=201)
def create_assessment(body: AssessmentCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    return assessment_service.to_out(assessment_service.create_assessment(db, body, admin), include_questions=True)


@router.get("", response_model=list[AssessmentOut])
def list_assessments(
    status: str | None = Query(None, pattern="^(draft|published|archived)$"), db: Session = Depends(get_db)
):
    return [assessment_service.to_out(a) for a in assessment_service.list_assessments(db, status)]


@router.get("/{assessment_id}", response_model=AssessmentDetail)
def get_assessment(assessment_id: int, db: Session = Depends(get_db)):
    return assessment_service.to_out(assessment_service.get_assessment(db, assessment_id), include_questions=True)


@router.patch("/{assessment_id}", response_model=AssessmentDetail)
def update_assessment(assessment_id: int, body: AssessmentUpdate, db: Session = Depends(get_db)):
    return assessment_service.to_out(assessment_service.update_assessment(db, assessment_id, body), include_questions=True)


@router.post("/{assessment_id}/generate-questions", response_model=AssessmentDetail)
def generate_questions(assessment_id: int, body: GenerateQuestionsRequest, db: Session = Depends(get_db)):
    """Generate and append requested objective questions to a draft."""
    assessment = assessment_service.generate_questions(db, assessment_id, body)
    return assessment_service.to_out(assessment, include_questions=True)


@router.post("/{assessment_id}/publish", response_model=AssessmentDetail)
def publish(assessment_id: int, db: Session = Depends(get_db)):
    return assessment_service.to_out(assessment_service.publish_assessment(db, assessment_id), include_questions=True)


@router.post("/{assessment_id}/archive", response_model=AssessmentDetail)
def archive(assessment_id: int, db: Session = Depends(get_db)):
    return assessment_service.to_out(assessment_service.archive_assessment(db, assessment_id), include_questions=True)


@router.post("/{assessment_id}/duplicate", response_model=AssessmentDetail, status_code=201)
def duplicate(assessment_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    return assessment_service.to_out(assessment_service.duplicate_assessment(db, assessment_id, admin), include_questions=True)


@router.post("/{assessment_id}/preview")
def preview(assessment_id: int, db: Session = Depends(get_db)):
    """Exactly what a student would receive: no correct answers, rubrics or explanations."""
    assessment = assessment_service.get_assessment(db, assessment_id)
    return {
        "title": assessment.title,
        "instructions": assessment.instructions,
        "duration_minutes": assessment.duration_minutes,
        "questions": [
            {
                "number": n,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "points": float(q.points),
                "options": [{"id": o["id"], "text": o["text"]} for o in q.options_json] if q.options_json else None,
            }
            for n, q in enumerate(assessment.questions, start=1)
        ],
    }
