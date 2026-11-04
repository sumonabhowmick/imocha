"""Batches (named groups of candidates) and bulk candidate import from CSV."""
from __future__ import annotations

import csv
import io

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import bad_request, conflict, not_found
from app.models import Batch, BatchMember, Candidate, User
from app.services import auth_service, candidate_service

MAX_IMPORT_ROWS = 2000
MAX_IMPORT_CHARS = 1_000_000


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------
def _batch_out(batch: Batch, member_count: int) -> dict:
    return {
        "id": batch.id,
        "name": batch.name,
        "description": batch.description,
        "member_count": member_count,
        "created_at": batch.created_at,
    }


def list_batches(db: Session) -> list[dict]:
    counts = dict(db.execute(select(BatchMember.batch_id, func.count()).group_by(BatchMember.batch_id)).all())
    return [_batch_out(b, counts.get(b.id, 0)) for b in db.scalars(select(Batch).order_by(Batch.name))]


def get_batch(db: Session, batch_id: int) -> Batch:
    batch = db.get(Batch, batch_id)
    if not batch:
        raise not_found("Batch")
    return batch


def batch_detail(db: Session, batch_id: int) -> dict:
    batch = get_batch(db, batch_id)
    members = list(
        db.scalars(
            select(Candidate)
            .join(BatchMember, BatchMember.candidate_id == Candidate.id)
            .where(BatchMember.batch_id == batch_id)
            .order_by(Candidate.name)
        )
    )
    out = _batch_out(batch, len(members))
    out["members"] = [
        {"id": c.id, "name": c.name, "email": c.email, "is_active": c.is_active} for c in members
    ]
    return out


def create_batch(db: Session, name: str, description: str, admin: User) -> dict:
    name = name.strip()
    if db.scalar(select(Batch.id).where(func.lower(Batch.name) == name.lower())):
        raise conflict("A batch with this name already exists.")
    batch = Batch(name=name, description=description.strip(), created_by=admin.id)
    db.add(batch)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise conflict("A batch with this name already exists.") from exc
    return _batch_out(batch, 0)


def update_batch(db: Session, batch_id: int, name: str | None, description: str | None) -> dict:
    batch = get_batch(db, batch_id)
    if name is not None and name.strip() and name.strip() != batch.name:
        if db.scalar(select(Batch.id).where(func.lower(Batch.name) == name.strip().lower(), Batch.id != batch_id)):
            raise conflict("A batch with this name already exists.")
        batch.name = name.strip()
    if description is not None:
        batch.description = description.strip()
    db.commit()
    return batch_detail(db, batch_id)


def delete_batch(db: Session, batch_id: int) -> None:
    """Deletes the group only. Candidates, assignments and results are untouched."""
    db.delete(get_batch(db, batch_id))
    db.commit()


def add_members(db: Session, batch_id: int, candidate_ids: list[int]) -> dict:
    get_batch(db, batch_id)
    ids = list(dict.fromkeys(candidate_ids))
    found = set(db.scalars(select(Candidate.id).where(Candidate.id.in_(ids))))
    missing = [i for i in ids if i not in found]
    if missing:
        raise bad_request(f"Unknown candidate ids: {missing}")
    existing = set(db.scalars(select(BatchMember.candidate_id).where(BatchMember.batch_id == batch_id, BatchMember.candidate_id.in_(ids))))
    for cid in ids:
        if cid not in existing:
            db.add(BatchMember(batch_id=batch_id, candidate_id=cid))
    db.commit()
    return batch_detail(db, batch_id)


def remove_member(db: Session, batch_id: int, candidate_id: int) -> dict:
    member = db.scalar(select(BatchMember).where(BatchMember.batch_id == batch_id, BatchMember.candidate_id == candidate_id))
    if not member:
        raise not_found("Batch member")
    db.delete(member)
    db.commit()
    return batch_detail(db, batch_id)


def active_member_ids(db: Session, batch_id: int) -> list[int]:
    get_batch(db, batch_id)
    return list(
        db.scalars(
            select(Candidate.id)
            .join(BatchMember, BatchMember.candidate_id == Candidate.id)
            .where(BatchMember.batch_id == batch_id, Candidate.is_active.is_(True))
            .order_by(Candidate.name)
        )
    )


def assign_batch(db: Session, assessment_id: int, batch_id: int, max_attempts: int | None, admin: User):
    """Assign an assessment to every active member of a batch (existing assignments kept)."""
    ids = active_member_ids(db, batch_id)
    if not ids:
        raise bad_request("This batch has no active candidates.")
    return candidate_service.assign_candidates(db, assessment_id, ids, max_attempts, admin)


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------
def _parse_csv(text: str) -> list[dict]:
    if len(text) > MAX_IMPORT_CHARS:
        raise bad_request("The CSV file is too large (limit about 1 MB).")
    text = text.lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        raise bad_request("The CSV file is empty.")
    header = [c.strip().lower() for c in rows[0]]
    if "email" in header:
        name_col = next((header.index(h) for h in ("name", "full name", "full_name", "student name") if h in header), None)
        email_col = header.index("email")
        body = rows[1:]
    else:  # no header: name,email
        name_col, email_col, body = 0, 1, rows
    if len(body) > MAX_IMPORT_ROWS:
        raise bad_request(f"Too many rows (limit {MAX_IMPORT_ROWS}).")
    parsed = []
    for line_no, r in enumerate(body, start=2 if body is not rows else 1):
        email = r[email_col].strip() if email_col < len(r) else ""
        name = r[name_col].strip() if name_col is not None and name_col < len(r) else ""
        parsed.append({"line": line_no, "name": name, "email": email})
    return parsed


def import_candidates(db: Session, csv_text: str, batch_id: int | None, dry_run: bool) -> dict:
    """Validate (and unless dry_run, save) candidates from CSV text with columns name,email.

    Existing candidates are matched by email and reused (never duplicated). With a
    batch_id, every valid row (new or existing) is added to that batch.
    """
    if batch_id is not None:
        get_batch(db, batch_id)
    rows = _parse_csv(csv_text)
    existing = {
        c.email: c
        for c in db.scalars(select(Candidate).where(Candidate.email.in_([auth_service.normalize_email(r["email"]) for r in rows if r["email"]])))
    }
    seen: set[str] = set()
    report = []
    for r in rows:
        status, reason, email = "new", "", auth_service.normalize_email(r["email"])
        try:
            validate_email(r["email"], check_deliverability=False)
        except EmailNotValidError:
            status, reason = "invalid", "Invalid email address."
        if status != "invalid" and not r["name"]:
            status, reason = "invalid", "Name is missing."
        if status != "invalid" and len(r["name"]) > 120:
            status, reason = "invalid", "Name is longer than 120 characters."
        if status != "invalid" and email in seen:
            status, reason = "duplicate", "Same email appears earlier in the file."
        if status == "new" and email in existing:
            status, reason = "existing", f"Already a candidate ({existing[email].name}); will be reused."
        if status in ("new", "existing"):
            seen.add(email)
        report.append({"line": r["line"], "name": r["name"], "email": email, "status": status, "reason": reason})

    summary = {s: sum(1 for x in report if x["status"] == s) for s in ("new", "existing", "duplicate", "invalid")}
    if dry_run:
        return {"dry_run": True, "summary": summary, "rows": report}

    created_ids, member_ids = [], []
    for row in report:
        if row["status"] == "new":
            candidate = Candidate(name=row["name"], email=row["email"])
            db.add(candidate)
            db.flush()
            created_ids.append(candidate.id)
            member_ids.append(candidate.id)
        elif row["status"] == "existing":
            member_ids.append(existing[row["email"]].id)
    db.commit()
    if batch_id is not None and member_ids:
        add_members(db, batch_id, member_ids)
    return {"dry_run": False, "summary": summary, "rows": report, "created_ids": created_ids, "candidate_ids": member_ids}
