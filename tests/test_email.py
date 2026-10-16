import logging
import smtplib

from app.models import EmailLog
from app.services import email_service
from tests.helpers import assign, create_assessment, create_candidate, full_flow, setup_admin


def test_invitation_email_has_html_and_text(client, mailbox):
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    c = create_candidate(client, headers)
    assign(client, headers, a["id"], c["id"])
    msg = mailbox.to(c["email"])[0]
    assert msg.get_body(preferencelist=("plain",)) is not None
    assert msg.get_body(preferencelist=("html",)) is not None
    assert "AssessFlow AI" in mailbox.html_of(msg)


def test_result_email_contains_score_and_feedback(client, make_client, mailbox):
    headers = setup_admin(client)
    flow = full_flow(client, headers, make_client, mailbox, answers={0: {"selected": "B"}, 1: {"selected": "B"}})
    results = [m for m in mailbox.to(flow["candidate"]["email"]) if m["Subject"].startswith("Your results")]
    assert len(results) == 1
    text = mailbox.text_of(results[0])
    assert "Score: 5 / 5" in text and "Percentage: 100.00%" in text and "Competitive" in text
    assert "Keep going!" in text  # personalised feedback from the (fake) model
    # Admin-only coaching content never reaches the student.
    assert "Pair on exception handling" not in text and "coaching" not in text.lower()


def test_html_email_escapes_untrusted_content(client, make_client, mailbox, fake_ai):
    import json

    from tests.conftest import STUDENT_JSON

    fake_ai.set("student", json.dumps({**STUDENT_JSON, "summary": "<script>alert(1)</script>"}))
    headers = setup_admin(client)
    flow = full_flow(client, headers, make_client, mailbox, answers={0: {"selected": "B"}})
    msg = [m for m in mailbox.to(flow["candidate"]["email"]) if m["Subject"].startswith("Your results")][0]
    html = mailbox.html_of(msg)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_smtp_failure_is_recorded_not_reported_as_sent(client, mailbox, db):
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    c = create_candidate(client, headers)
    mailbox.fail(smtplib.SMTPRecipientsRefused({c["email"]: (550, b"no such user")}))
    res = client.post(
        f"/api/assessments/{a['id']}/assign", json={"candidate_ids": [c["id"]], "send_invitations": True}, headers=headers
    )
    assert res.status_code == 200 and res.json()["invitations"][0]["email_status"] == "failed"
    log = db.query(EmailLog).one()
    assert log.status == "failed" and log.sent_at is None and "refused" in log.last_error


def test_transient_smtp_failure_retried_automatically(client, mailbox, db, monkeypatch):
    FakeSMTP = email_service.smtplib.SMTP  # the fake installed by the mailbox fixture

    calls = {"n": 0}
    original = FakeSMTP.send_message

    def flaky(self, msg):
        calls["n"] += 1
        if calls["n"] == 1:
            raise smtplib.SMTPServerDisconnected("dropped")
        return original(self, msg)

    monkeypatch.setattr(FakeSMTP, "send_message", flaky)
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    c = create_candidate(client, headers)
    assign(client, headers, a["id"], c["id"])
    log = db.query(EmailLog).one()
    assert log.status == "sent" and log.attempts == 2


def test_admin_can_retry_failed_invitation(client, make_client, mailbox, db):
    from tests.helpers import verify

    headers = setup_admin(client)
    a = create_assessment(client, headers)
    c = create_candidate(client, headers)
    mailbox.fail(smtplib.SMTPServerDisconnected("down"))
    assign(client, headers, a["id"], c["id"])
    log_id = db.query(EmailLog).one().id
    mailbox.fail(None)
    res = client.post(f"/api/admin/email-logs/{log_id}/retry", headers=headers)
    assert res.status_code == 200 and res.json()["status"] == "sent"
    # The retry issued fresh credentials, which work.
    token, passcode = mailbox.credentials_for(c["email"])
    assert verify(make_client(), token, passcode).status_code == 200
    # A delivered email cannot be "retried" again.
    assert client.post(f"/api/admin/email-logs/{log_id}/retry", headers=headers).status_code == 409


def test_failed_result_email_retry_sends_once(client, make_client, mailbox, db):
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    c = create_candidate(client, headers)
    assign(client, headers, a["id"], c["id"])
    from tests.helpers import start, student_session, submit

    s = student_session(make_client, mailbox, c["email"])
    attempt = start(s).json()["attempt_id"]
    mailbox.fail(smtplib.SMTPResponseException(554, b"rejected"))
    submit(s, attempt)
    log = db.query(EmailLog).filter_by(notification_type="result").one()
    assert log.status == "failed"
    mailbox.fail(None)
    assert client.post(f"/api/admin/email-logs/{log.id}/retry", headers=headers).json()["status"] == "sent"
    assert client.post(f"/api/admin/email-logs/{log.id}/retry", headers=headers).status_code == 409
    sent = [m for m in mailbox.to(c["email"]) if m["Subject"].startswith("Your results")]
    assert len(sent) == 1
    assert db.query(EmailLog).filter_by(notification_type="result").count() == 1


def test_secrets_never_logged(client, make_client, mailbox, caplog):
    from tests.helpers import verify

    caplog.set_level(logging.DEBUG)
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    c = create_candidate(client, headers)
    assign(client, headers, a["id"], c["id"])
    token, passcode = mailbox.credentials_for(c["email"])
    verify(make_client(), token, "000000" if passcode != "000000" else "111111")
    verify(make_client(), token, passcode)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert token not in logged and passcode not in logged
    assert "Correct-Horse-42!" not in logged


def test_admin_notified_with_dashboard_link_only(client, make_client, mailbox):
    headers = setup_admin(client)
    full_flow(client, headers, make_client, mailbox, answers={0: {"selected": "B"}})
    notices = mailbox.to("admin@example.com")
    assert notices and notices[0]["Subject"].startswith("Result ready")
    text = mailbox.text_of(notices[0])
    assert "http://localhost:8501" in text or "dashboard" in text.lower()
    assert "Keep going!" not in text  # no feedback contents in admin notices
