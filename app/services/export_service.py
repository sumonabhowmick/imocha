"""Result exports (CSV and Excel) for one assessment.

One row per assigned candidate with the latest attempt's verified score, category,
status, proctoring flag count and per-question points. Values that start with =, +, -
or @ are prefixed with an apostrophe so spreadsheet apps never run them as formulas
(CSV injection protection).
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import Answer, AssessmentAssignment, Attempt, Batch, BatchMember
from app.services import proctoring_service
from app.services.assessment_service import get_assessment
from app.services.email_service import format_local

DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value):
    if isinstance(value, str) and value.startswith(DANGEROUS_PREFIXES):
        return "'" + value
    return value


def _rows(db: Session, assessment_id: int) -> tuple[list[str], list[list]]:
    assessment = get_assessment(db, assessment_id)
    questions = list(assessment.questions)
    assignments = list(
        db.scalars(
            select(AssessmentAssignment)
            .where(AssessmentAssignment.assessment_id == assessment_id)
            .options(
                joinedload(AssessmentAssignment.candidate),
                selectinload(AssessmentAssignment.attempts).selectinload(Attempt.result),
            )
            .order_by(AssessmentAssignment.id)
        ).unique()
    )
    batches: dict[int, list[str]] = defaultdict(list)
    for cid, name in db.execute(
        select(BatchMember.candidate_id, Batch.name).join(Batch, Batch.id == BatchMember.batch_id).order_by(Batch.name)
    ).all():
        batches[cid].append(name)
    latest = {a.id: max(a.attempts, key=lambda t: t.attempt_number) for a in assignments if a.attempts}
    attempt_ids = [t.id for t in latest.values()]
    flags = proctoring_service.flag_counts(db, attempt_ids)
    points: dict[tuple[int, int], float | None] = {}
    if attempt_ids:
        for ans in db.scalars(select(Answer).where(Answer.attempt_id.in_(attempt_ids))):
            points[(ans.attempt_id, ans.question_id)] = float(ans.awarded_points) if ans.awarded_points is not None else None

    header = [
        "Candidate", "Email", "Batches", "Assignment status", "Attempts used", "Submitted at", "Auto-submitted",
        "Score", "Max score", "Percentage", "Category", "Result status", "Proctoring flags",
    ] + [f"Q{n} ({q.topic or 'General'}, {float(q.points):g} pts)" for n, q in enumerate(questions, start=1)]
    rows = []
    for a in assignments:
        attempt = latest.get(a.id)
        result = attempt.result if attempt else None
        row = [
            a.candidate.name,
            a.candidate.email,
            ", ".join(batches.get(a.candidate_id, [])),
            a.status,
            len(a.attempts),
            format_local(attempt.submitted_at) if attempt and attempt.submitted_at else "",
            ("yes" if attempt.auto_submitted else "no") if attempt and attempt.submitted_at else "",
            float(result.raw_score) if result else "",
            float(result.max_score) if result else "",
            float(result.percentage) if result and result.percentage is not None else "",
            (result.category or "") if result else "",
            result.result_status if result else "",
            flags.get(attempt.id, 0) if attempt else "",
        ]
        for q in questions:
            value = points.get((attempt.id, q.id)) if attempt else None
            row.append("" if value is None else value)
        rows.append([_safe(v) for v in row])
    return header, rows


def export_csv(db: Session, assessment_id: int) -> bytes:
    header, rows = _rows(db, assessment_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return ("﻿" + buffer.getvalue()).encode("utf-8")  # BOM so Excel opens UTF-8 correctly


def export_xlsx(db: Session, assessment_id: int) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    header, rows = _rows(db, assessment_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="12313A")
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    for column in ws.columns:
        width = max(len(str(c.value or "")) for c in column)
        ws.column_dimensions[column[0].column_letter].width = min(max(10, width + 2), 45)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
