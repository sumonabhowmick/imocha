"""Brevo HTTPS email backend and hosted-database URL handling (HTTP is mocked; nothing is sent)."""
from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.services import email_service
from app.services.email_service import EmailDeliveryError
from tests.helpers import assign, create_assessment, create_candidate, setup_admin

API_KEY = "xkeysib-test-not-real"


class FakeBrevo:
    def __init__(self, status=201):
        self.status = status
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if isinstance(self.status, Exception):
            raise self.status
        body = {"messageId": "<1@brevo>"} if self.status < 300 else {"message": "Sender not valid"}
        return httpx.Response(self.status, json=body)


@pytest.fixture
def brevo(settings, monkeypatch):
    monkeypatch.setattr(settings, "email_backend", "brevo")
    monkeypatch.setattr(settings, "brevo_api_key", API_KEY)
    monkeypatch.setattr(settings, "smtp_from_email", "me@gmail.com")
    fake = FakeBrevo()
    monkeypatch.setattr(email_service.httpx, "post", fake.post)
    return fake


def test_invitation_is_sent_through_brevo(client, brevo, mailbox):
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    c = create_candidate(client, headers, email="asha@example.com")
    assign(client, headers, a["id"], c["id"])

    (call,) = brevo.calls
    assert call["url"] == email_service.BREVO_URL
    assert call["headers"]["api-key"] == API_KEY
    assert call["json"]["to"] == [{"email": "asha@example.com"}]
    assert call["json"]["sender"]["email"] == "me@gmail.com"
    assert "Start Assessment" in call["json"]["htmlContent"]
    assert mailbox.messages == []  # SMTP was not used
    logs = client.get("/api/admin/email-logs", headers=headers).json()
    assert [(log["notification_type"], log["status"]) for log in logs] == [("invitation", "sent")]
    assert API_KEY not in str(logs)


def test_brevo_rejection_is_logged_without_secrets(client, brevo):
    brevo.status = 400
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    c = create_candidate(client, headers)
    assign(client, headers, a["id"], c["id"])
    (log,) = client.get("/api/admin/email-logs", headers=headers).json()
    assert log["status"] == "failed"
    assert "HTTP 400" in log["last_error"] and API_KEY not in log["last_error"]


def test_brevo_outage_is_retried(client, brevo, monkeypatch):
    responses = iter([503, 201])

    def flaky(url, json=None, headers=None, timeout=None):
        return httpx.Response(next(responses), json={})

    monkeypatch.setattr(email_service.httpx, "post", flaky)
    headers = setup_admin(client)
    a = create_assessment(client, headers)
    c = create_candidate(client, headers)
    assign(client, headers, a["id"], c["id"])
    (log,) = client.get("/api/admin/email-logs", headers=headers).json()
    assert log["status"] == "sent" and log["attempts"] == 2


def test_bad_api_key_and_network_errors(brevo):
    msg = email_service._build_message("x@example.com", "s", "<p>h</p>", "t")
    brevo.status = 401
    with pytest.raises(EmailDeliveryError, match="API key"):
        email_service._deliver_brevo(msg)
    brevo.status = httpx.ConnectError("boom")
    with pytest.raises(EmailDeliveryError) as info:
        email_service._deliver_brevo(msg)
    assert info.value.transient


def test_brevo_requires_api_key():
    with pytest.raises(ValueError, match="BREVO_API_KEY"):
        Settings(app_secret_key="k" * 40, database_url="postgresql://u:p@h/assessflow_x", email_backend="brevo", brevo_api_key="")


@pytest.mark.parametrize(
    "url",
    ["postgres://u:p@ep-x.neon.tech/assessflow_ai?sslmode=require", "postgresql://u:p@ep-x.neon.tech/assessflow_ai?sslmode=require"],
)
def test_hosted_database_urls_use_psycopg(url):
    assert Settings(app_secret_key="k" * 40, database_url=url).database_url == (
        "postgresql+psycopg://u:p@ep-x.neon.tech/assessflow_ai?sslmode=require"
    )


def test_render_hostname_is_trusted(monkeypatch):
    """Render's own address must pass the Host-header check."""
    import importlib

    import app.main as main_module

    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "assessflow-ai.onrender.com")
    reloaded = importlib.reload(main_module)
    from fastapi.testclient import TestClient

    with TestClient(reloaded.app, base_url="https://assessflow-ai.onrender.com") as c:
        assert c.get("/api/health").status_code == 200
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME")
    importlib.reload(main_module)
