"""Student-facing schemas. These must never carry correct answers, rubrics,
explanations, other candidates' data or admin feedback."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class VerifyRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    passcode: str = Field(min_length=4, max_length=12, pattern=r"^\d+$")


class ResendCodeRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class VerifyResponse(BaseModel):
    message: str
    csrf_token: str


class StudentAssessmentOverview(BaseModel):
    candidate_name: str
    title: str
    description: str
    instructions: str
    category: str
    difficulty: str
    question_count: int
    total_points: float
    duration_minutes: int
    access_start_at: datetime | None
    access_end_at: datetime | None
    timezone: str
    access_start_display: str | None
    access_end_display: str | None
    completion_policy: str
    attempts_used: int
    max_attempts: int
    can_start: bool
    start_blocked_reason: str | None
    in_progress_attempt_id: int | None
    server_time: datetime


class AttemptOut(BaseModel):
    attempt_id: int
    attempt_number: int
    status: str
    started_at: datetime
    deadline_at: datetime
    remaining_seconds: int


class StudentOption(BaseModel):
    id: str
    text: str


class StudentQuestion(BaseModel):
    id: int
    number: int
    question_text: str
    question_type: str
    points: float
    options: list[StudentOption] | None = None
    saved_answer: dict | None = None


class AttemptQuestions(AttemptOut):
    title: str
    questions: list[StudentQuestion]


class AnswerIn(BaseModel):
    # mcq_single: "A"; mcq_multi: ["A", "C"]; text/coding use `text`. Both null = clear.
    selected: str | list[str] | None = None
    text: str | None = Field(None, max_length=20000)


class AnswerSaved(BaseModel):
    question_id: int
    saved: bool
    remaining_seconds: int


class SubmitResponse(BaseModel):
    attempt_id: int
    status: str
    submitted_at: datetime
    auto_submitted: bool
    already_submitted: bool
    message: str


class AttemptStatus(BaseModel):
    attempt_id: int
    status: str
    started_at: datetime
    deadline_at: datetime
    submitted_at: datetime | None
    remaining_seconds: int
    auto_submitted: bool
    result_ready: bool
