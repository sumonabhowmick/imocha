"""Admin-facing assessment and question schemas.

Student-facing question data lives in schemas/student.py and intentionally has no
correct-answer, rubric or explanation fields.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import AwareDatetimeMixin, ORMModel

QuestionType = Literal["mcq_single", "mcq_multi", "text", "coding"]
Difficulty = Literal["beginner", "intermediate", "advanced"]
CompletionPolicy = Literal["finish_after_window", "hard_end_at_window"]


class CategoryBand(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    min: float = Field(ge=0, le=100)
    max: float = Field(ge=0, le=100)


class AssessmentBase(AwareDatetimeMixin):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=5000)
    instructions: str = Field("", max_length=5000)
    category: str = Field("", max_length=100)
    difficulty: Difficulty = "intermediate"
    duration_minutes: int | None = Field(None, ge=1, le=600)
    access_start_at: datetime | None = None
    access_end_at: datetime | None = None
    completion_policy: CompletionPolicy | None = None
    max_attempts: int = Field(1, ge=1, le=10)
    category_scale: list[CategoryBand] | None = None


class AssessmentCreate(AssessmentBase):
    pass


class AssessmentUpdate(AwareDatetimeMixin):
    """All fields optional. Which fields may change depends on the assessment status."""

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)
    instructions: str | None = Field(None, max_length=5000)
    category: str | None = Field(None, max_length=100)
    difficulty: Difficulty | None = None
    duration_minutes: int | None = Field(None, ge=1, le=600)
    access_start_at: datetime | None = None
    access_end_at: datetime | None = None
    completion_policy: CompletionPolicy | None = None
    max_attempts: int | None = Field(None, ge=1, le=10)
    category_scale: list[CategoryBand] | None = None


class GenerateQuestionsRequest(BaseModel):
    mcq_count: int = Field(0, ge=0, le=50, description="Single-answer multiple-choice questions")
    maq_count: int = Field(0, ge=0, le=50, description="Multiple-answer questions")

    @model_validator(mode="after")
    def _check_counts(self) -> "GenerateQuestionsRequest":
        if self.mcq_count + self.maq_count == 0:
            raise ValueError("Request at least one question.")
        if self.mcq_count + self.maq_count > 50:
            raise ValueError("Generate at most 50 questions at a time.")
        return self


class OptionIn(BaseModel):
    id: str = Field(min_length=1, max_length=5, pattern=r"^[A-Za-z0-9]+$")
    text: str = Field(min_length=1, max_length=1000)


class QuestionBase(BaseModel):
    question_text: str = Field(min_length=1, max_length=5000)
    question_type: QuestionType
    topic: str = Field("", max_length=100)
    points: Decimal = Field(Decimal("1"), ge=0, le=1000, decimal_places=2)
    options: list[OptionIn] | None = None
    # mcq_single: "A"; mcq_multi: ["A", "C"]; text/coding: optional model answer.
    correct_answer: str | list[str] | None = None
    grading_rubric: str = Field("", max_length=5000)
    explanation: str = Field("", max_length=5000)


class QuestionCreate(QuestionBase):
    @model_validator(mode="after")
    def _check(self) -> "QuestionCreate":
        validate_question_fields(
            self.question_type, self.options, self.correct_answer
        )
        return self


class QuestionUpdate(BaseModel):
    question_text: str | None = Field(None, min_length=1, max_length=5000)
    question_type: QuestionType | None = None
    topic: str | None = Field(None, max_length=100)
    points: Decimal | None = Field(None, ge=0, le=1000, decimal_places=2)
    options: list[OptionIn] | None = None
    correct_answer: str | list[str] | None = None
    grading_rubric: str | None = Field(None, max_length=5000)
    explanation: str | None = Field(None, max_length=5000)


class ReorderRequest(BaseModel):
    question_ids: list[int] = Field(min_length=1)


def validate_question_fields(
    question_type: str, options: list[OptionIn] | list[dict] | None, correct_answer
) -> None:
    """Shared validation for created and updated questions."""
    if question_type in ("mcq_single", "mcq_multi"):
        if not options or len(options) < 2:
            raise ValueError("Multiple-choice questions need at least 2 options.")
        ids = [o.id if isinstance(o, OptionIn) else o["id"] for o in options]
        if len(set(ids)) != len(ids):
            raise ValueError("Option ids must be unique.")
        if question_type == "mcq_single":
            if not isinstance(correct_answer, str) or correct_answer not in ids:
                raise ValueError("correct_answer must be one of the option ids.")
        else:
            if (
                not isinstance(correct_answer, list)
                or not correct_answer
                or len(set(correct_answer)) != len(correct_answer)
                or any(c not in ids for c in correct_answer)
            ):
                raise ValueError("correct_answer must be a non-empty list of distinct option ids.")
    else:
        if options:
            raise ValueError("Text and coding questions do not have options.")
        if correct_answer is not None and not isinstance(correct_answer, str):
            raise ValueError("For text questions, correct_answer is an optional model answer string.")


class QuestionOut(ORMModel):
    """Admin view of a question, including private grading data."""

    id: int
    assessment_id: int
    position: int
    question_text: str
    question_type: str
    topic: str
    points: float
    options: list[dict] | None = None
    correct_answer: str | list[str] | None = None
    grading_rubric: str
    explanation: str


class AssessmentOut(BaseModel):
    id: int
    title: str
    description: str
    instructions: str
    category: str
    difficulty: str
    duration_minutes: int
    access_start_at: datetime | None
    access_end_at: datetime | None
    completion_policy: str
    max_attempts: int
    status: str  # stored status: draft / published / archived
    display_status: str  # draft / scheduled / active / completed / archived
    category_scale: list[dict]
    question_count: int
    total_points: float
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AssessmentDetail(AssessmentOut):
    questions: list[QuestionOut]
