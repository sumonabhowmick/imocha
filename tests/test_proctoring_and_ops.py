"""Proctoring, reminders, exports, audit log and security hardening."""
from __future__ import annotations

import base64
import csv
import io
from datetime import timedelta

from openpyxl import load_workbook
from sqlalchemy import func, select, update

from app.models import Assessment, AuditEvent, EmailLog, ProctoringEvent
from app.services import proctoring_service, reminder_service
from app.workflows import assessment_workflow as wf
from tests.helpers import ADMIN_EMAIL, ADMIN_PASSWORD, assign, create_assessment, create_candidate, full_flow, now, setup_admin
from tests.test_portal import csrf, portal_login

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200  # minimal JPEG-looking bytes
JPEG_B64 = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()


def proctored_attempt(client, headers, make_client, mailbox, consent=True):
    assessment = create_assessment(client, headers)
    candidate = create_candidate(client, headers)
    assignment_id = assign(client, headers, assessment["id"], candidate["id"], invite=False)
    browser = portal_login(make_client, mailbox, candidate["email"])
    res = browser.post(f"/api/portal/assessments/{assignment_id}/attempts", json={"proctoring_consent": consent}, headers=csrf(browser))
    return browser, res, assessment


# ---------------------------------------------------------------------------
# Proctoring
# ---------------------------------------------------------------------------
def test_required_proctoring_needs_consent(client, make_client, mailbox):
    headers = setup_admin(client)
    _b, res, _a = proctored_attempt(client, headers, make_client, mailbox, consent=False)
    assert res.status_code == 403 and res.json()["code"] == "proctoring_required"


def test_events_snapshots_and_admin_timeline(client, make_client, mailbox, db):
    headers = setup_admin(client)
    browser, res, _a = proctored_attempt(client, headers, make_client, mailbox)
    attempt_id = res.json()["attempt_id"]
    url = f"/api/portal/attempts/{attempt_id}/proctoring/events"

    ok = browser.post(url, json={"event_type": "face_missing", "duration_seconds": 12, "snapshot": JPEG_B64}, headers=csrf(browser))
    assert ok.status_code == 201
    browser.post(url, json={"event_type": "tab_hidden"}, headers=csrf(browser))
    browser.post(url, json={"event_type": "face_returned"}, headers=csrf(browser))
    # Rejected: unknown type, snapshot on a non-flag event, non-JPEG snapshot, no CSRF.
    assert browser.post(url, json={"event_type": "hacked"}, headers=csrf(browser)).status_code == 400
    assert browser.post(url, json={"event_type": "face_returned", "snapshot": JPEG_B64}, headers=csrf(browser)).status_code == 400
    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG....").decode()
    assert browser.post(url, json={"event_type": "multiple_faces", "snapshot": png}, headers=csrf(browser)).status_code == 400
    assert browser.post(url, json={"event_type": "tab_hidden"}).status_code == 403

    timeline = client.get(f"/api/admin/attempts/{attempt_id}/proctoring", headers=headers).json()
    assert timeline["flag_count"] == 2 and timeline["face_missing_seconds"] == 12
    assert timeline["consent_at"] is not None
    snap_event = next(e for e in timeline["events"] if e["has_snapshot"])
    image = client.get(f"/api/admin/proctoring/snapshots/{snap_event['id']}", headers=headers)
    assert image.status_code == 200 and image.content == JPEG and image.headers["content-type"] == "image/jpeg"
    assert browser.get(f"/api/admin/proctoring/snapshots/{snap_event['id']}").status_code == 401  # students cannot view
    assert db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.action == "proctoring.snapshot_viewed")) == 1


def test_flags_never_change_the_score(client, make_client, mailbox):
    headers = setup_admin(client)
    browser, res, assessment = proctored_attempt(client, headers, make_client, mailbox)
    attempt_id = res.json()["attempt_id"]
    for _ in range(3):
        browser.post(f"/api/portal/attempts/{attempt_id}/proctoring/events", json={"event_type": "multiple_faces"}, headers=csrf(browser))
    qs = browser.get(f"/api/portal/attempts/{attempt_id}/questions").json()["questions"]
    for q in qs:
        browser.put(f"/api/portal/attempts/{attempt_id}/answers/{q['id']}", json={"selected": "B"}, headers=csrf(browser))
    browser.post(f"/api/portal/attempts/{attempt_id}/submit", headers=csrf(browser))
    row = client.get("/api/admin/results", headers=headers).json()["items"][0]
    assert row["percentage"] == 100.0 and row["proctoring_flags"] == 3
    # Events are refused once the attempt is submitted.
    late = browser.post(f"/api/portal/attempts/{attempt_id}/proctoring/events", json={"event_type": "tab_hidden"}, headers=csrf(browser))
    assert late.status_code == 409


def test_camera_is_mandatory_on_every_start_route(client, make_client, mailbox):
    """No assessment can be started without camera consent - through the portal or the
    original invitation-link endpoint - and admins cannot switch proctoring off."""
    headers = setup_admin(client)
    assessment = create_assessment(client, headers)
    assert assessment["proctoring_mode"] == "required"
    off = client.patch(f"/api/assessments/{assessment['id']}", json={"proctoring_mode": "off"}, headers=headers).json()
    assert off["proctoring_mode"] == "required"  # the setting no longer exists; it is ignored

    candidate = create_candidate(client, headers)
    assign(client, headers, assessment["id"], candidate["id"])
    from tests.helpers import csrf as invite_csrf, student_session

    student = student_session(make_client, mailbox, candidate["email"])
    no_body = student.post("/api/student/attempts", headers=invite_csrf(student))
    refused = student.post("/api/student/attempts", json={"proctoring_consent": False}, headers=invite_csrf(student))
    assert no_body.status_code == refused.status_code == 403 and refused.json()["code"] == "proctoring_required"
    ok = student.post("/api/student/attempts", json={"proctoring_consent": True}, headers=invite_csrf(student))
    assert ok.status_code == 200
    # Resuming also needs consent.
    assert student.post("/api/student/attempts", json={}, headers=invite_csrf(student)).status_code == 403


def test_old_snapshots_are_purged(client, make_client, mailbox, db):
    headers = setup_admin(client)
    browser, res, _a = proctored_attempt(client, headers, make_client, mailbox)
    attempt_id = res.json()["attempt_id"]
    browser.post(f"/api/portal/attempts/{attempt_id}/proctoring/events", json={"event_type": "face_missing", "snapshot": JPEG_B64}, headers=csrf(browser))
    db.execute(update(ProctoringEvent).values(occurred_at=now() - timedelta(days=60)))
    db.commit()
    assert proctoring_service.purge_old_snapshots(db) == 1
    assert db.scalar(select(func.count(ProctoringEvent.id)).where(ProctoringEvent.snapshot.is_not(None))) == 0


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------
def test_reminder_sent_once_to_students_who_have_not_started(client, make_client, mailbox, db):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers, reminder_minutes_before_end=60, end=now() + timedelta(minutes=30))
    waiting = create_candidate(client, headers, name="Waiting", email="waiting@example.com")
    started = create_candidate(client, headers, name="Started", email="started@example.com")
    assign(client, headers, assessment["id"], waiting["id"])
    started_assignment = assign(client, headers, assessment["id"], started["id"], invite=False)
    browser = portal_login(make_client, mailbox, "started@example.com")
    browser.post(f"/api/portal/assessments/{started_assignment}/attempts", json={"proctoring_consent": True}, headers=csrf(browser))

    wf.sweep_once()
    wf.sweep_once()  # second sweep must not send again
    reminders = [m for m in mailbox.messages if m["Subject"].startswith("Reminder:")]
    assert [m["To"] for m in reminders] == ["waiting@example.com"]
    body = mailbox.text_of(reminders[0])
    assert "/portal" in body and "token=" not in body  # no secrets in reminders


def test_no_reminder_before_the_threshold(client, db):
    headers = setup_admin(client)
    assessment = create_assessment(client, headers, reminder_minutes_before_end=30, end=now() + timedelta(hours=2))
    c = create_candidate(client, headers)
    assign(client, headers, assessment["id"], c["id"], invite=False)
    assert reminder_service.send_due_reminders(db) == 0


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
def test_csv_and_excel_exports(client, make_client, mailbox, db):
    headers = setup_admin(client)
    flow = full_flow(client, headers, make_client, mailbox, answers={0: {"selected": "B"}}, name="=HYPERLINK(evil)")
    aid = flow["assessment"]["id"]

    res = client.get(f"/api/admin/assessments/{aid}/export?format=csv", headers=headers)
    assert res.status_code == 200 and "attachment" in res.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(res.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["Candidate", "Email", "Batches"] and rows[0][-2].startswith("Q1")
    assert rows[1][0] == "'=HYPERLINK(evil)"  # formula injection neutralised
    assert rows[1][9] == "40.0" and rows[1][-2] == "2.0"

    xlsx = client.get(f"/api/admin/assessments/{aid}/export?format=xlsx", headers=headers)
    sheet = load_workbook(io.BytesIO(xlsx.content)).active
    assert sheet["A1"].value == "Candidate" and sheet["J2"].value == 40.0
    assert client.get(f"/api/admin/assessments/{aid}/export?format=pdf", headers=headers).status_code == 422
    assert client.get(f"/api/admin/assessments/{aid}/export").status_code == 401


# ---------------------------------------------------------------------------
# Audit log and security hardening
# ---------------------------------------------------------------------------
def test_audit_log_records_key_actions(client, db):
    headers = setup_admin(client)
    client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password-1"})
    create_assessment(client, headers)
    events = client.get("/api/admin/audit", headers=headers).json()["items"]
    actions = {e["action"] for e in events}
    assert {"admin.login", "admin.login_failed", "assessment.publish"} <= actions
    text = str(events)
    assert ADMIN_PASSWORD not in text and "wrong-password-1" not in text


def test_security_headers_size_limit_and_host_check(client):
    res = client.get("/api/health")
    assert "camera=(self)" in res.headers["permissions-policy"]
    assert res.headers["cross-origin-opener-policy"] == "same-origin"
    assert "'wasm-unsafe-eval'" in res.headers["content-security-policy"]
    big = client.post("/api/auth/login", content=b"x" * 10, headers={"content-length": "50000000", "content-type": "application/json"})
    assert big.status_code == 413
    assert client.get("/api/health", headers={"host": "evil.example.com"}).status_code == 400


def test_ai_actions_are_rate_limited(client, settings, monkeypatch):
    headers = setup_admin(client)
    monkeypatch.setattr(settings, "agent_messages_per_10_minutes", 2)
    codes = [client.post("/api/admin/agents/chat", json={"message": "hi"}, headers=headers).status_code for _ in range(3)]
    assert codes[-1] == 429


def test_admin_cookie_login_requires_csrf_for_changes(client, make_client):
    setup_admin(client)
    browser = make_client()
    res = browser.post("/api/auth/session", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert res.status_code == 200 and "access_token" not in res.json()
    assert browser.get("/api/auth/me").status_code == 200  # cookie works for reads
    body = {"name": "Neha", "email": "neha@example.com"}
    assert browser.post("/api/candidates", json=body).status_code == 403  # no CSRF header
    ok = browser.post("/api/candidates", json=body, headers={"X-CSRF-Token": browser.cookies.get("af_admin_csrf")})
    assert ok.status_code == 201
    browser.post("/api/auth/logout", headers={"X-CSRF-Token": browser.cookies.get("af_admin_csrf")})
    assert browser.get("/api/auth/me").status_code == 401


def test_assessment_settings_round_trip(client, db):
    headers = setup_admin(client)
    a = create_assessment(client, headers, shuffle_questions=True, reminder_minutes_before_end=45)
    assert (a["shuffle_questions"], a["proctoring_mode"], a["reminder_minutes_before_end"]) == (True, "required", 45)
    updated = client.patch(f"/api/assessments/{a['id']}", json={"reminder_minutes_before_end": 30}, headers=headers).json()
    assert updated["reminder_minutes_before_end"] == 30  # allowed after publish (affects new attempts)
    assert db.get(Assessment, a["id"]).proctoring_mode == "required"
    dup = client.post(f"/api/assessments/{a['id']}/duplicate", headers=headers).json()
    assert dup["proctoring_mode"] == "required" and dup["shuffle_questions"] is True
    assert db.scalar(select(func.count(EmailLog.id)).where(EmailLog.notification_type == "reminder")) == 0
