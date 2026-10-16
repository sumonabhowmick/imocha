"""Read-side reporting: results, question-level detail and assessment analytics.

All statistics are computed here in Python from database rows. The AI model only ever
receives these computed numbers; it never produces statistics itself.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.errors import not_found
from app.models import (
    Answer,
    Assessment,
    AssessmentAssignment,
    Attempt,
    Candidate,
    EmailLog,
    Feedback,
    Invitation,
    Question,
    Result,
)
from app.services.assessment_service import category_scale_for, get_assessment


def _fmt(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _answer_display(question: Question, answer_json: dict | None) -> str | None:
    """Human-readable form of a student's answer."""
    if not answer_json:
        return None
    if "text" in answer_json:
        return answer_json["text"]
    options = {o["id"]: o["text"] for o in (question.options_json or [])}
    selected = answer_json.get("selected")
    if isinstance(selected, list):
        return "; ".join(f"{s}. {options.get(s, '?')}" for s in selected)
    if selected:
        return f"{selected}. {options.get(selected, '?')}"
    return None


def _correct_display(question: Question) -> str | None:
    if question.correct_answer is None:
        return None
    if question.question_type in ("mcq_single", "mcq_multi"):
        return _answer_display(question, {"selected": question.correct_answer})
    return str(question.correct_answer)


def answer_outcome(answer: Answer) -> str:
    if answer.needs_review or answer.awarded_points is None:
        return "pending_review"
    if not answer.answer_json:
        return "unanswered"
    if answer.is_correct:
        return "correct"
    if answer.awarded_points and answer.awarded_points > 0:
        return "partial"
    return "incorrect"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
def _result_query():
    return (
        select(Result, Attempt, AssessmentAssignment, Assessment, Candidate)
        .join(Attempt, Result.attempt_id == Attempt.id)
        .join(AssessmentAssignment, Attempt.assignment_id == AssessmentAssignment.id)
        .join(Assessment, AssessmentAssignment.assessment_id == Assessment.id)
        .join(Candidate, AssessmentAssignment.candidate_id == Candidate.id)
    )


def _row(result: Result, attempt: Attempt, assessment: Assessment, candidate: Candidate) -> dict:
    return {
        "result_id": result.id,
        "attempt_id": attempt.id,
        "attempt_number": attempt.attempt_number,
        "assessment_id": assessment.id,
        "assessment_title": assessment.title,
        "candidate_id": candidate.id,
        "candidate_name": candidate.name,
        "candidate_email": candidate.email,
        "raw_score": _fmt(result.raw_score),
        "max_score": _fmt(result.max_score),
        "percentage": _fmt(result.percentage),
        "category": result.category,
        "result_status": result.result_status,
        "grading_method": result.grading_method,
        "correct_count": result.correct_count,
        "incorrect_count": result.incorrect_count,
        "unanswered_count": result.unanswered_count,
        "pending_review_count": result.pending_review_count,
        "started_at": attempt.started_at,
        "submitted_at": attempt.submitted_at,
        "auto_submitted": attempt.auto_submitted,
        "calculated_at": result.calculated_at,
    }


def list_results(
    db: Session,
    assessment_id: int | None = None,
    candidate_id: int | None = None,
    result_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    stmt = _result_query()
    count_stmt = (
        select(func.count(Result.id))
        .join(Attempt, Result.attempt_id == Attempt.id)
        .join(AssessmentAssignment, Attempt.assignment_id == AssessmentAssignment.id)
    )
    if assessment_id:
        stmt = stmt.where(Assessment.id == assessment_id)
        count_stmt = count_stmt.where(AssessmentAssignment.assessment_id == assessment_id)
    if candidate_id:
        stmt = stmt.where(Candidate.id == candidate_id)
        count_stmt = count_stmt.where(AssessmentAssignment.candidate_id == candidate_id)
    if result_status:
        stmt = stmt.where(Result.result_status == result_status)
        count_stmt = count_stmt.where(Result.result_status == result_status)
    rows = db.execute(stmt.order_by(Result.calculated_at.desc()).limit(limit).offset(offset)).all()
    result_ids = [r[0].id for r in rows]
    feedback_status: dict[tuple[int, str], str] = {}
    email_status: dict[int, str] = {}
    if result_ids:
        for fb in db.scalars(select(Feedback).where(Feedback.result_id.in_(result_ids))):
            feedback_status[(fb.result_id, fb.feedback_type)] = fb.generation_status
        for log in db.scalars(
            select(EmailLog).where(EmailLog.related_result_id.in_(result_ids), EmailLog.notification_type == "result")
        ):
            email_status[log.related_result_id] = log.status
    items = []
    for result, attempt, _assignment, assessment, candidate in rows:
        row = _row(result, attempt, assessment, candidate)
        row["student_feedback_status"] = feedback_status.get((result.id, "student"))
        row["admin_feedback_status"] = feedback_status.get((result.id, "admin"))
        row["result_email_status"] = email_status.get(result.id)
        items.append(row)
    return {"total": db.scalar(count_stmt) or 0, "items": items}


def result_detail(db: Session, result_id: int) -> dict:
    row = db.execute(_result_query().where(Result.id == result_id)).first()
    if not row:
        raise not_found("Result")
    result, attempt, _assignment, assessment, candidate = row
    data = _row(result, attempt, assessment, candidate)
    answers = {
        a.question_id: a
        for a in db.scalars(
            select(Answer).where(Answer.attempt_id == attempt.id).options(joinedload(Answer.question))
        )
    }
    assessment = get_assessment(db, assessment.id)
    questions = []
    for number, q in enumerate(assessment.questions, start=1):
        a = answers.get(q.id)
        questions.append(
            {
                "number": number,
                "question_id": q.id,
                "answer_id": a.id if a else None,
                "topic": q.topic,
                "question_type": q.question_type,
                "question_text": q.question_text,
                "student_answer": _answer_display(q, a.answer_json) if a else None,
                "correct_answer": _correct_display(q),
                "max_points": _fmt(a.max_points) if a else float(q.points),
                "awarded_points": _fmt(a.awarded_points) if a else None,
                "outcome": answer_outcome(a) if a else "unanswered",
                "grading_method": a.grading_method if a else None,
                "grading_model": a.grading_model if a else None,
                "grading_rationale": a.grading_rationale if a else None,
                "needs_review": a.needs_review if a else False,
            }
        )
    data["questions"] = questions
    data["category_scale"] = category_scale_for(assessment)
    email = db.scalar(
        select(EmailLog).where(EmailLog.related_result_id == result.id, EmailLog.notification_type == "result")
    )
    data["result_email_status"] = email.status if email else None
    return data


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def _latest_results_per_assignment(db: Session, assessment_id: int) -> dict[int, tuple[Result, Attempt]]:
    latest: dict[int, tuple[Result, Attempt]] = {}
    for result, attempt in db.execute(
        select(Result, Attempt)
        .join(Attempt, Result.attempt_id == Attempt.id)
        .join(AssessmentAssignment, Attempt.assignment_id == AssessmentAssignment.id)
        .where(AssessmentAssignment.assessment_id == assessment_id)
        .order_by(Attempt.attempt_number)
    ).all():
        latest[attempt.assignment_id] = (result, attempt)
    return latest


def assessment_analytics(db: Session, assessment_id: int) -> dict:
    assessment = get_assessment(db, assessment_id)
    assignments = list(
        db.scalars(
            select(AssessmentAssignment)
            .where(AssessmentAssignment.assessment_id == assessment_id)
            .options(joinedload(AssessmentAssignment.candidate), selectinload(AssessmentAssignment.attempts))
        ).unique()
    )
    ids = [a.id for a in assignments]
    invitations = list(db.scalars(select(Invitation).where(Invitation.assignment_id.in_(ids)))) if ids else []
    latest_inv: dict[int, Invitation] = {}
    for inv in sorted(invitations, key=lambda i: i.created_at):
        latest_inv[inv.assignment_id] = inv
    sent_assignments = set()
    failed_email_assignments = set()
    if ids:
        for log in db.scalars(
            select(EmailLog).where(EmailLog.related_assignment_id.in_(ids), EmailLog.notification_type == "invitation")
        ):
            if log.status in ("sent", "outbox"):
                sent_assignments.add(log.related_assignment_id)
            elif log.status == "failed":
                failed_email_assignments.add(log.related_assignment_id)
    latest_results = _latest_results_per_assignment(db, assessment_id)

    started = sum(1 for a in assignments if a.attempts)
    completed = sum(1 for a in assignments if a.id in latest_results)
    revoked = sum(1 for a in assignments if a.status == "revoked")
    expired_or_failed = sum(
        1
        for a in assignments
        if a.id not in latest_results
        and (
            (a.id in latest_inv and latest_inv[a.id].status == "expired")
            or (a.id in failed_email_assignments and a.id not in sent_assignments)
        )
    )

    # Only final results with a valid percentage count towards score statistics.
    valid = [
        (aid, r) for aid, (r, _att) in latest_results.items() if r.result_status == "final" and r.percentage is not None
    ]
    percentages = [float(r.percentage) for _aid, r in valid]
    distribution = Counter()
    for p in percentages:
        bucket = min(int(p // 10) * 10, 90)
        distribution[f"{bucket}-{bucket + 10}%"] += 1
    categories = Counter(r.category for _aid, r in valid if r.category)

    # Per-topic and per-question performance across valid results.
    valid_attempt_ids = [r.attempt_id for _aid, r in valid]
    topic_points: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    question_stats: dict[int, dict] = {}
    q_number = {q.id: n for n, q in enumerate(assessment.questions, start=1)}
    per_attempt_topics: dict[int, dict[str, list[Decimal]]] = defaultdict(lambda: defaultdict(lambda: [Decimal(0), Decimal(0)]))
    if valid_attempt_ids:
        for answer in db.scalars(
            select(Answer).where(Answer.attempt_id.in_(valid_attempt_ids)).options(joinedload(Answer.question))
        ):
            topic = answer.question.topic or "General"
            earned = answer.awarded_points or Decimal(0)
            topic_points[topic][0] += earned
            topic_points[topic][1] += answer.max_points
            per_attempt_topics[answer.attempt_id][topic][0] += earned
            per_attempt_topics[answer.attempt_id][topic][1] += answer.max_points
            qs = question_stats.setdefault(
                answer.question_id,
                {
                    "number": q_number.get(answer.question_id),
                    "topic": topic,
                    "question_text": answer.question.question_text[:200],
                    "responses": 0,
                    "full_credit": 0,
                    "unanswered": 0,
                },
            )
            qs["responses"] += 1
            if answer.is_correct:
                qs["full_credit"] += 1
            if not answer.answer_json:
                qs["unanswered"] += 1
    topics = [
        {
            "topic": topic,
            "points_earned": float(earned),
            "points_possible": float(possible),
            "average_percent": round(float(earned / possible * 100), 2) if possible else None,
        }
        for topic, (earned, possible) in sorted(topic_points.items())
    ]
    questions = sorted(
        (
            {**qs, "full_credit_rate_percent": round(qs["full_credit"] / qs["responses"] * 100, 2)}
            for qs in question_stats.values()
        ),
        key=lambda q: q["number"] or 0,
    )

    candidates = []
    assignment_by_id = {a.id: a for a in assignments}
    for aid, r in sorted(valid, key=lambda x: x[1].percentage):
        weak = [
            t
            for t, (e, p) in per_attempt_topics[r.attempt_id].items()
            if p and (e / p) < Decimal("0.5")
        ]
        candidates.append(
            {
                "candidate_id": assignment_by_id[aid].candidate_id,
                "candidate_name": assignment_by_id[aid].candidate.name,
                "result_id": r.id,
                "percentage": float(r.percentage),
                "category": r.category,
                "topics_below_50_percent": sorted(weak),
            }
        )

    return {
        "assessment_id": assessment.id,
        "assessment_title": assessment.title,
        "total_assigned": len(assignments),
        "invitations_sent": len(sent_assignments),
        "started": started,
        "completed": completed,
        "pending": len(assignments) - completed - revoked,
        "revoked": revoked,
        "expired_or_failed_invitations": expired_or_failed,
        "provisional_results": sum(1 for r, _a in latest_results.values() if r.result_status == "provisional"),
        "valid_results": len(percentages),
        "average_percent": round(sum(percentages) / len(percentages), 2) if percentages else None,
        "highest_percent": max(percentages) if percentages else None,
        "lowest_percent": min(percentages) if percentages else None,
        "score_distribution": dict(sorted(distribution.items(), key=lambda kv: int(kv[0].split("-")[0]))),
        "category_counts": dict(categories),
        "category_scale": category_scale_for(assessment),
        "topics": topics,
        "questions": questions,
        "candidates": candidates,
    }


def overview(db: Session) -> dict:
    final_pcts = [
        float(p)
        for p in db.scalars(
            select(Result.percentage).where(Result.result_status == "final", Result.percentage.is_not(None))
        )
    ]
    assignment_status = dict(
        db.execute(select(AssessmentAssignment.status, func.count()).group_by(AssessmentAssignment.status)).all()
    )
    category_counts = dict(
        db.execute(
            select(Result.category, func.count())
            .where(Result.category.is_not(None))
            .group_by(Result.category)
        ).all()
    )
    return {
        "total_assessments": db.scalar(select(func.count(Assessment.id))) or 0,
        "published_assessments": db.scalar(
            select(func.count(Assessment.id)).where(Assessment.status == "published")
        ) or 0,
        "total_candidates": db.scalar(select(func.count(Candidate.id))) or 0,
        "total_assignments": sum(assignment_status.values()),
        "total_invitations": db.scalar(select(func.count(Invitation.id))) or 0,
        "started_attempts": db.scalar(select(func.count(Attempt.id))) or 0,
        "completed_assessments": db.scalar(select(func.count(Result.id))) or 0,
        "pending_assessments": sum(
            n for status, n in assignment_status.items() if status in ("assigned", "invited", "in_progress")
        ),
        "average_score_percent": round(sum(final_pcts) / len(final_pcts), 2) if final_pcts else None,
        "results_needing_review": db.scalar(
            select(func.count(Result.id)).where(Result.result_status == "provisional")
        ) or 0,
        "failed_emails": db.scalar(select(func.count(EmailLog.id)).where(EmailLog.status == "failed")) or 0,
        "failed_feedback": db.scalar(
            select(func.count(Feedback.id)).where(Feedback.generation_status == "failed")
        ) or 0,
        "assignment_status_counts": assignment_status,
        "category_counts": category_counts,
    }
