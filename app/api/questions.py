from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin
from app.schemas.assessment import AssessmentDetail, QuestionCreate, QuestionOut, QuestionUpdate, ReorderRequest
from app.services import assessment_service

router = APIRouter(prefix="/api", tags=["questions"], dependencies=[Depends(get_current_admin)])


@router.post("/assessments/{assessment_id}/questions", response_model=QuestionOut, status_code=201)
def add_question(assessment_id: int, body: QuestionCreate, db: Session = Depends(get_db)):
    return assessment_service.question_to_out(assessment_service.add_question(db, assessment_id, body))


@router.post("/assessments/{assessment_id}/questions/reorder", response_model=AssessmentDetail)
def reorder(assessment_id: int, body: ReorderRequest, db: Session = Depends(get_db)):
    assessment = assessment_service.reorder_questions(db, assessment_id, body.question_ids)
    return assessment_service.to_out(assessment, include_questions=True)


@router.patch("/questions/{question_id}", response_model=QuestionOut)
def update_question(question_id: int, body: QuestionUpdate, db: Session = Depends(get_db)):
    return assessment_service.question_to_out(assessment_service.update_question(db, question_id, body))


@router.delete("/questions/{question_id}", status_code=204)
def delete_question(question_id: int, db: Session = Depends(get_db)):
    assessment_service.delete_question(db, question_id)
    return Response(status_code=204)
