"""Question bank endpoints (admin only)."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin
from app.models import User
from app.schemas.assessment import QuestionCreate, QuestionUpdate
from app.services import bank_service

router = APIRouter(prefix="/api", tags=["question bank"], dependencies=[Depends(get_current_admin)])

Difficulty = Literal["beginner", "intermediate", "advanced"]


class BankQuestionCreate(QuestionCreate):
    difficulty: Difficulty = "intermediate"


class BankQuestionUpdate(QuestionUpdate):
    difficulty: Difficulty | None = None


class AddFromBankRequest(BaseModel):
    bank_question_ids: list[int] = Field(min_length=1, max_length=200)


class RandomPickRequest(BaseModel):
    count: int = Field(ge=1, le=100)
    topic: str | None = Field(None, max_length=100)
    difficulty: Difficulty | None = None
    question_type: Literal["mcq_single", "mcq_multi", "text", "coding"] | None = None


class SaveToBankRequest(BaseModel):
    difficulty: Difficulty = "intermediate"


@router.get("/bank/questions")
def list_bank(
    search: str | None = Query(None, max_length=200),
    topic: str | None = Query(None, max_length=100),
    question_type: str | None = Query(None, pattern="^(mcq_single|mcq_multi|text|coding)$"),
    difficulty: str | None = Query(None, pattern="^(beginner|intermediate|advanced)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return bank_service.list_bank(db, search, topic, question_type, difficulty, limit, offset)


@router.post("/bank/questions", status_code=201)
def create_bank_question(body: BankQuestionCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    data = QuestionCreate.model_validate(body.model_dump(exclude={"difficulty"}))
    return bank_service.create_bank_question(db, data, body.difficulty, admin)


@router.get("/bank/questions/{bank_id}")
def get_bank_question(bank_id: int, db: Session = Depends(get_db)):
    return bank_service.bank_to_out(bank_service.get_bank_question(db, bank_id))


@router.patch("/bank/questions/{bank_id}")
def update_bank_question(bank_id: int, body: BankQuestionUpdate, db: Session = Depends(get_db)):
    return bank_service.update_bank_question(db, bank_id, body.model_dump(exclude_unset=True))


@router.delete("/bank/questions/{bank_id}", status_code=204)
def delete_bank_question(bank_id: int, db: Session = Depends(get_db)):
    bank_service.delete_bank_question(db, bank_id)


@router.post("/assessments/{assessment_id}/questions/from-bank")
def add_from_bank(assessment_id: int, body: AddFromBankRequest, db: Session = Depends(get_db)):
    """Copy chosen bank questions into a draft assessment."""
    return bank_service.add_to_assessment(db, assessment_id, body.bank_question_ids)


@router.post("/assessments/{assessment_id}/questions/random-from-bank")
def random_from_bank(assessment_id: int, body: RandomPickRequest, db: Session = Depends(get_db)):
    """Copy N random matching bank questions into a draft assessment."""
    return bank_service.random_pick(db, assessment_id, body.count, body.topic, body.difficulty, body.question_type)


@router.post("/questions/{question_id}/save-to-bank", status_code=201)
def save_to_bank(question_id: int, body: SaveToBankRequest, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    return bank_service.save_question_to_bank(db, question_id, body.difficulty, admin)
