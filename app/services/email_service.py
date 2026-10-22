"""Email rendering and delivery (SMTP with TLS) plus delivery logging.

Rules enforced here:
* Every email has an HTML and a plain-text part.
* An EmailLog row records the status; passcodes, tokens and bodies are never logged.
* A message is only marked "sent" after the SMTP server accepts it.
* At most one result email exists per result (unique index); retries reuse that row.
"""
from __future__ import annotations

import logging
import smtplib
import socket
import ssl
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import utcnow
from app.models import Assessment, AssessmentAssignment, Attempt, Candidate, EmailLog, Feedback, Result, User

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
    trim_blocks=True,
    lstrip_blocks=True,
)


class EmailDeliveryError(Exception):
    """Raised with a safe, content-free description of a delivery failure."""

    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def format_local(dt: datetime | None) -> str:
    if dt is None:
        return "Not scheduled"
    local = dt.astimezone(get_settings().tz)
    return local.strftime("%a, %d %b %Y, %I:%M %p ") + (local.tzname() or "")


def _render(template: str, **context) -> tuple[str, str]:
    context.setdefault("app_name", "AssessFlow AI")
    context.setdefault("support_contact", get_settings().support_contact)
    html = _env.get_template(f"{template}.html").render(**context)
    text = _env.get_template(f"{template}.txt").render(**context)
    return html, text


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def _build_message(to: str, subject: str, html: str, text: str) -> EmailMessage:
    settings = get_settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("AssessFlow AI", settings.smtp_from_email or "no-reply@assessflow.local"))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain="assessflow.local")
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def _smtp_session(send: EmailMessage | None) -> None:
    """Connect, upgrade to TLS, log in and (optionally) send one message.

    With `send=None` it only verifies the connection and credentials. Credentials are
    passed straight to smtplib and never logged or included in error messages.
    """
    settings = get_settings()
    if not settings.smtp_configured:
        raise EmailDeliveryError("SMTP is not configured (set SMTP_HOST and SMTP_FROM_EMAIL in .env).")
    context = ssl.create_default_context()
    is_gmail = settings.smtp_host.lower().endswith("gmail.com")
    try:
        if settings.smtp_use_ssl:
            server = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds, context=context
            )
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds)
        with server:
            if not settings.smtp_use_ssl:
                server.starttls(context=context)  # refuse to send credentials or mail without TLS
            if settings.smtp_username:
                # Gmail displays app passwords in groups ("abcd efgh ..."); the spaces are not part of it.
                password = settings.smtp_password.replace(" ", "") if is_gmail else settings.smtp_password
                server.login(settings.smtp_username, password)
            if send is not None:
                refused = server.send_message(send)
                if refused:
                    raise EmailDeliveryError("The SMTP server refused the recipient.")
    except smtplib.SMTPAuthenticationError as exc:
        hint = (
            " For Gmail, use a 16-character App Password (Google Account > Security > 2-Step Verification"
            " > App passwords), not your normal password, and set SMTP_USERNAME to the full Gmail address."
            if is_gmail
            else ""
        )
        raise EmailDeliveryError(f"SMTP authentication failed (code {exc.smtp_code}).{hint}") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise EmailDeliveryError("The SMTP server refused the recipient address.") from exc
    except smtplib.SMTPResponseException as exc:
        transient = 400 <= exc.smtp_code < 500
        raise EmailDeliveryError(f"SMTP error code {exc.smtp_code}.", transient=transient) from exc
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, socket.timeout, ConnectionError) as exc:
        raise EmailDeliveryError(f"Could not reach the SMTP server ({type(exc).__name__}).", transient=True) from exc
    except smtplib.SMTPException as exc:
        raise EmailDeliveryError(f"SMTP failure ({type(exc).__name__}).") from exc
    except OSError as exc:
        raise EmailDeliveryError(f"Network error while sending email ({type(exc).__name__}).", transient=True) from exc


def _deliver_smtp(msg: EmailMessage) -> None:
    _smtp_session(msg)


def check_smtp_connection() -> None:
    """Verify SMTP host, TLS and login without sending anything. Raises EmailDeliveryError."""
    _smtp_session(None)


def send_test_email(to: str) -> None:
    """Send a harmless test message (no codes, tokens or personal data). Raises EmailDeliveryError."""
    settings = get_settings()
    html, text = _render(
        "admin_notice",
        headline="This is a test email from AssessFlow AI. If you received it, SMTP delivery works.",
        candidate_name="(test)",
        assessment_title="(test)",
        result_status="n/a",
        pending_review=0,
        dashboard_url=settings.dashboard_url,
        result_id="n/a",
    )
    _smtp_session(_build_message(to, "AssessFlow AI - SMTP test email", html, text))


def _deliver_outbox(msg: EmailMessage, log_id: int) -> None:
    """Development-only: save the email as a .eml file instead of sending it."""
    outbox = Path(get_settings().email_outbox_dir)
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    (outbox / f"{stamp}-email-{log_id}.eml").write_bytes(bytes(msg))


def deliver(db: Session, log: EmailLog, html: str, text: str, max_tries: int = 2) -> EmailLog:
    """Send the message for `log`, updating its status. Commits the log row."""
    settings = get_settings()
    msg = _build_message(log.recipient_email, log.subject, html, text)
    for attempt in range(1, max_tries + 1):
        log.attempts += 1
        try:
            if settings.email_backend == "outbox":
                _deliver_outbox(msg, log.id)
                log.status = "outbox"
            else:
                _deliver_smtp(msg)
                log.status = "sent"
            log.sent_at = utcnow()
            log.last_error = None
            break
        except EmailDeliveryError as exc:
            log.status = "failed"
            log.last_error = str(exc)[:500]
            if not exc.transient or attempt == max_tries:
                break
            time.sleep(min(2 * attempt, 5))
    db.commit()
    logger.info("Email log %s (%s): %s", log.id, log.notification_type, log.status)
    return log


def new_log(
    db: Session,
    recipient: str,
    notification_type: str,
    subject: str,
    assignment_id: int | None = None,
    result_id: int | None = None,
) -> EmailLog:
    log = EmailLog(
        recipient_email=recipient,
        notification_type=notification_type,
        subject=subject[:255],
        related_assignment_id=assignment_id,
        related_result_id=result_id,
        status="queued",
    )
    db.add(log)
    db.flush()
    return log


# ---------------------------------------------------------------------------
# Invitation and passcode emails (raw secrets exist only in memory here)
# ---------------------------------------------------------------------------
def invitation_link(token: str) -> str:
    # The token goes in the URL fragment (#...), which browsers never send to the server,
    # so it cannot appear in server or proxy access logs. The passcode is never in the URL.
    return f"{get_settings().public_base_url.rstrip('/')}/start#token={token}"


def send_invitation_email(
    db: Session, assignment: AssessmentAssignment, token: str, passcode: str, log: EmailLog | None = None
) -> EmailLog:
    settings = get_settings()
    assessment: Assessment = assignment.assessment
    candidate: Candidate = assignment.candidate
    subject = f"Your assessment invitation: {assessment.title}"
    html, text = _render(
        "invitation",
        candidate_name=candidate.name,
        assessment_title=assessment.title,
        scheduled_date=assessment.access_start_at.astimezone(settings.tz).strftime("%A, %d %B %Y")
        if assessment.access_start_at
        else "To be announced",
        window_start=format_local(assessment.access_start_at),
        window_end=format_local(assessment.access_end_at),
        timezone=settings.app_timezone,
        duration_minutes=assessment.duration_minutes,
        start_url=invitation_link(token),
        passcode=passcode,
        passcode_ttl=settings.passcode_ttl_minutes,
        finish_after_window=assessment.completion_policy == "finish_after_window",
    )
    if log is None:
        log = new_log(db, candidate.email, "invitation", subject, assignment_id=assignment.id)
    else:
        log.subject = subject
    return deliver(db, log, html, text)


def send_passcode_email(db: Session, assignment: AssessmentAssignment, passcode: str) -> EmailLog:
    settings = get_settings()
    subject = f"Your AssessFlow AI verification code for {assignment.assessment.title}"
    html, text = _render(
        "passcode",
        candidate_name=assignment.candidate.name,
        assessment_title=assignment.assessment.title,
        passcode=passcode,
        passcode_ttl=settings.passcode_ttl_minutes,
    )
    log = new_log(db, assignment.candidate.email, "passcode", subject, assignment_id=assignment.id)
    return deliver(db, log, html, text)


# ---------------------------------------------------------------------------
# Result email (student) - at most one per result
# ---------------------------------------------------------------------------
def _result_context(db: Session, result: Result) -> dict:
    attempt = db.get(Attempt, result.attempt_id)
    assignment = db.get(AssessmentAssignment, attempt.assignment_id)
    assessment = db.get(Assessment, assignment.assessment_id)
    candidate = db.get(Candidate, assignment.candidate_id)
    feedback = db.scalar(
        select(Feedback).where(Feedback.result_id == result.id, Feedback.feedback_type == "student")
    )
    content = feedback.content if feedback and feedback.generation_status == "completed" else None
    return {
        "assignment": assignment,
        "candidate": candidate,
        "candidate_name": candidate.name,
        "assessment_title": assessment.title,
        "raw_score": f"{result.raw_score.normalize():f}",
        "max_score": f"{result.max_score.normalize():f}",
        "percentage": f"{result.percentage:.2f}" if result.percentage is not None else None,
        "category": result.category,
        "result_status": result.result_status,
        "submitted_at": format_local(attempt.submitted_at),
        "feedback": content,
        "ai_assisted": result.grading_method in ("ai_assisted", "mixed"),
    }


def send_result_email(db: Session, result_id: int, allow_without_feedback: bool = False) -> EmailLog | None:
    """Send the student's result email once. Returns None if it was already delivered or
    if feedback is not ready and `allow_without_feedback` is False."""
    result = db.get(Result, result_id)
    if result is None or result.result_status != "final":
        return None
    log = db.scalar(
        select(EmailLog)
        .where(EmailLog.related_result_id == result_id, EmailLog.notification_type == "result")
        .with_for_update()
    )
    if log and log.status in ("sent", "outbox"):
        return None  # duplicate prevention
    ctx = _result_context(db, result)
    if ctx["feedback"] is None and not allow_without_feedback:
        return None
    subject = f"Your results: {ctx['assessment_title']}"
    if log is None:
        try:
            log = new_log(
                db,
                ctx["candidate"].email,
                "result",
                subject,
                assignment_id=ctx["assignment"].id,
                result_id=result_id,
            )
        except IntegrityError:  # another worker created it concurrently
            db.rollback()
            return None
    html, text = _render("result", **{k: v for k, v in ctx.items() if k not in ("assignment", "candidate")})
    return deliver(db, log, html, text)


# ---------------------------------------------------------------------------
# Admin notifications (links to the dashboard, no report contents)
# ---------------------------------------------------------------------------
def _admin_recipient(db: Session, assessment: Assessment) -> str | None:
    settings = get_settings()
    if settings.admin_notification_email:
        return settings.admin_notification_email
    if assessment.created_by:
        admin = db.get(User, assessment.created_by)
        if admin and admin.is_active:
            return admin.email
    return None


def notify_admin(db: Session, result: Result, kind: str) -> EmailLog | None:
    """kind: 'admin_result_ready' or 'admin_feedback_failed'. Sent once per result and kind."""
    already = db.scalar(
        select(EmailLog).where(
            EmailLog.related_result_id == result.id,
            EmailLog.notification_type == kind,
            EmailLog.status.in_(("sent", "outbox")),
        )
    )
    if already:
        return None
    attempt = db.get(Attempt, result.attempt_id)
    assignment = db.get(AssessmentAssignment, attempt.assignment_id)
    assessment = db.get(Assessment, assignment.assessment_id)
    candidate = db.get(Candidate, assignment.candidate_id)
    recipient = _admin_recipient(db, assessment)
    if not recipient:
        return None
    if kind == "admin_result_ready":
        subject = f"Result ready: {candidate.name} - {assessment.title}"
        headline = "A candidate has completed an assessment and the result is ready."
    else:
        subject = f"Action needed: feedback generation failed for {candidate.name}"
        headline = "AI feedback generation failed. The verified score is saved; you can retry from the dashboard."
    html, text = _render(
        "admin_notice",
        headline=headline,
        candidate_name=candidate.name,
        assessment_title=assessment.title,
        result_status=result.result_status,
        pending_review=result.pending_review_count,
        dashboard_url=get_settings().dashboard_url,
        result_id=result.id,
    )
    log = new_log(db, recipient, kind, subject, assignment_id=assignment.id, result_id=result.id)
    return deliver(db, log, html, text)


def resend_admin_notice(db: Session, log: EmailLog) -> EmailLog:
    result = db.get(Result, log.related_result_id) if log.related_result_id else None
    if result is None:
        log.status = "failed"
        log.last_error = "The related result no longer exists."
        db.commit()
        return log
    attempt = db.get(Attempt, result.attempt_id)
    assignment = db.get(AssessmentAssignment, attempt.assignment_id)
    assessment = db.get(Assessment, assignment.assessment_id)
    candidate = db.get(Candidate, assignment.candidate_id)
    html, text = _render(
        "admin_notice",
        headline="Notification (retry).",
        candidate_name=candidate.name,
        assessment_title=assessment.title,
        result_status=result.result_status,
        pending_review=result.pending_review_count,
        dashboard_url=get_settings().dashboard_url,
        result_id=result.id,
    )
    return deliver(db, log, html, text)
