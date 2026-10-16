"""Structured AI output schemas. Every model response is validated against one of these
before anything is stored. Fields are bounded so a misbehaving model cannot store
unbounded text."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

def _items(max_items: int = 8):
    return Field(default_factory=list, max_length=max_items)


class StudentFeedbackContent(BaseModel):
    summary: str = Field(min_length=1, max_length=1200)
    strengths: list[str] = _items()
    improvement_areas: list[str] = _items()
    recommendations: list[str] = _items()
    practice_areas: list[str] = _items()
    next_steps: list[str] = _items(6)
    closing_message: str = Field(min_length=1, max_length=600)
    evidence_limitations: str | None = Field(None, max_length=800)


class FollowUpItem(BaseModel):
    question_number: int | None = None
    topic: str = Field("", max_length=100)
    reason: str = Field(min_length=1, max_length=600)


class AdminFeedbackContent(BaseModel):
    overview: str = Field(min_length=1, max_length=1500)
    strong_areas: list[str] = _items()
    weak_areas: list[str] = _items()
    likely_learning_gaps: list[str] = _items()
    follow_up_items: list[FollowUpItem] = _items(15)
    coaching_actions: list[str] = _items()
    practice_topics: list[str] = _items()
    evidence_sufficiency: Literal["sufficient", "limited", "insufficient"]
    evidence_explanation: str = Field(min_length=1, max_length=1000)
    limitations: str | None = Field(None, max_length=1000)


class SupportCandidate(BaseModel):
    candidate_ref: str = Field(min_length=1, max_length=20)  # pseudonymous ref, e.g. "C3"
    evidence: str = Field(min_length=1, max_length=600)


class GroupFeedbackContent(BaseModel):
    overview: str = Field(min_length=1, max_length=1500)
    common_strengths: list[str] = _items()
    common_gaps: list[str] = _items()
    training_priorities: list[str] = _items()
    candidates_needing_support: list[SupportCandidate] = _items(50)
    limitations: str | None = Field(None, max_length=1000)


class TextGradeContent(BaseModel):
    awarded_points: float
    rationale: str = Field(min_length=1, max_length=1500)
    confidence: Literal["high", "medium", "low"]
    needs_review: bool = False


class GeneratedOption(BaseModel):
    id: str = Field(min_length=1, max_length=5, pattern=r"^[A-Za-z0-9]+$")
    text: str = Field(min_length=1, max_length=1000)


class GeneratedQuestion(BaseModel):
    question_type: Literal["mcq_single", "mcq_multi"]
    question_text: str = Field(min_length=1, max_length=5000)
    topic: str = Field(min_length=1, max_length=100)
    options: list[GeneratedOption] = Field(min_length=4, max_length=6)
    correct_answer: str | list[str]
    explanation: str = Field(min_length=1, max_length=2000)


class GeneratedQuestionSet(BaseModel):
    questions: list[GeneratedQuestion] = Field(min_length=1, max_length=50)


class FeedbackOut(BaseModel):
    id: int
    feedback_type: str
    generation_status: str
    content: dict | None
    model_name: str | None
    error_message: str | None
    generated_at: str | None
