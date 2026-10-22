"""SMTP connection checks and Gmail-specific handling (uses the fake SMTP from conftest)."""
from __future__ import annotations

import smtplib

import pytest

from app.services import email_service
from app.services.email_service import EmailDeliveryError, check_smtp_connection, send_test_email

SECRET = "abcd efgh ijkl mnop"


@pytest.fixture
def gmail(settings, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_username", "me@gmail.com")
    monkeypatch.setattr(settings, "smtp_password", SECRET)
    monkeypatch.setattr(settings, "smtp_from_email", "me@gmail.com")
    return settings


def test_check_only_logs_in_and_sends_nothing(gmail, mailbox, monkeypatch):
    logins = []
    fake = email_service.smtplib.SMTP
    monkeypatch.setattr(fake, "login", lambda self, user, pw: logins.append((user, pw)), raising=False)
    check_smtp_connection()
    assert logins == [("me@gmail.com", "abcdefghijklmnop")]  # Gmail app-password spaces removed
    assert mailbox.messages == []


def test_send_test_email_contains_no_secrets(gmail, mailbox):
    send_test_email("me@gmail.com")
    (msg,) = mailbox.messages
    assert msg["To"] == "me@gmail.com" and "test email" in msg["Subject"].lower()
    body = mailbox.text_of(msg) + mailbox.html_of(msg)
    assert SECRET not in body and "abcdefghijklmnop" not in body


def test_gmail_auth_failure_explains_app_password_without_leaking_it(gmail, monkeypatch):
    def reject(self, user, pw):
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    monkeypatch.setattr(email_service.smtplib.SMTP, "login", reject, raising=False)
    with pytest.raises(EmailDeliveryError) as info:
        check_smtp_connection()
    message = str(info.value)
    assert "code 535" in message and "App Password" in message
    assert SECRET not in message and "abcdefghijklmnop" not in message


def test_missing_smtp_settings_fail_clearly(settings, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    with pytest.raises(EmailDeliveryError, match="SMTP is not configured"):
        check_smtp_connection()
