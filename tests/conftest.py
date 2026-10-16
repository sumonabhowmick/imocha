"""Test configuration.

* Uses a SEPARATE PostgreSQL database (TEST_DATABASE_URL). The schema is rebuilt with
  Alembic migrations at the start and every table is truncated before each test.
* SMTP is replaced by an in-memory fake; OpenAI is replaced by a scripted LangChain chat
  model. No test needs real credentials, sends real email, or calls OpenAI.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
_env_file = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or _env_file.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise RuntimeError("Set TEST_DATABASE_URL (a separate database, e.g. .../assessflow_test) to run the tests.")
_db_name = TEST_DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
if not _db_name.endswith("_test") or TEST_DATABASE_URL == _env_file.get("DATABASE_URL"):
    raise RuntimeError("Refusing to run: TEST_DATABASE_URL must be a separate database whose name ends with '_test'.")

# Configure the app BEFORE it is imported. Values here override anything in .env.
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": TEST_DATABASE_URL,
        "APP_SECRET_KEY": "test-secret-key-that-is-long-enough-0123456789",
        "PUBLIC_BASE_URL": "http://testserver",
        "EMAIL_BACKEND": "smtp",
        "SMTP_HOST": "smtp.test.invalid",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "",
        "SMTP_PASSWORD": "",
        "SMTP_FROM_EMAIL": "noreply@assessflow.test",
        "ADMIN_NOTIFICATION_EMAIL": "",
        # Fake values: the scripted model below is always installed, so no request is sent.
        "OPENAI_API_KEY": "sk-test-not-a-real-key",
        "OPENAI_MODEL": "test-model",
        "AI_TEXT_GRADING_ENABLED": "false",
        "SWEEPER_INTERVAL_SECONDS": "0",
        "PASSCODE_RESEND_COOLDOWN_SECONDS": "0",
        "DEMO_MODE": "false",
    }
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import rate_limiter  # noqa: E402
from app.services import email_service  # noqa: E402
from app.services.ai_service import ai_service  # noqa: E402


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables(migrated_database):
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    rate_limiter.reset()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Fake SMTP
# ---------------------------------------------------------------------------
class FakeSMTP:
    sent: list = []
    fail_with: Exception | None = None

    def __init__(self, host, port, timeout=None, **kwargs):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        if FakeSMTP.fail_with is not None:
            raise FakeSMTP.fail_with
        FakeSMTP.sent.append(msg)
        return {}


class Mailbox:
    @property
    def messages(self):
        return FakeSMTP.sent

    def fail(self, exc: Exception | None):
        FakeSMTP.fail_with = exc

    def to(self, address: str):
        return [m for m in FakeSMTP.sent if m["To"] == address]

    @staticmethod
    def text_of(msg) -> str:
        return msg.get_body(preferencelist=("plain",)).get_content()

    @staticmethod
    def html_of(msg) -> str:
        return msg.get_body(preferencelist=("html",)).get_content()

    def credentials_for(self, address: str) -> tuple[str, str]:
        """(invitation token, passcode) from the latest invitation email to `address`."""
        invites = [m for m in self.to(address) if "invitation" in m["Subject"].lower()]
        assert invites, f"no invitation email for {address}"
        body = self.text_of(invites[-1])
        token = re.search(r"#token=([A-Za-z0-9_\-]+)", body).group(1)
        passcode = re.search(r"verification code: (\d+)", body).group(1)
        return token, passcode

    def latest_code_for(self, address: str) -> str:
        msg = self.to(address)[-1]
        return re.search(r"(?:code(?: for \"[^\"]*\")? is|verification code): (\d+)", self.text_of(msg)).group(1)


@pytest.fixture(autouse=True)
def mailbox(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.fail_with = None
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(email_service.time, "sleep", lambda s: None)
    return Mailbox()


# ---------------------------------------------------------------------------
# Scripted LangChain chat model (replaces OpenAI)
# ---------------------------------------------------------------------------
STUDENT_JSON = {
    "summary": "You scored well on the questions you answered.",
    "strengths": ["Question 1 (Basics) was answered correctly."],
    "improvement_areas": ["Question 2 (Exceptions) needs more practice."],
    "recommendations": ["Review how exceptions are raised and caught."],
    "practice_areas": ["Exception handling"],
    "next_steps": ["Practise try/except blocks", "Retake a short quiz"],
    "closing_message": "Keep going!",
    "evidence_limitations": "This quiz had only a few questions.",
}
ADMIN_JSON = {
    "overview": "Candidate answered basics correctly but missed exception handling.",
    "strong_areas": ["Basics (Q1)"],
    "weak_areas": ["Exceptions (Q2)"],
    "likely_learning_gaps": ["Exception flow control (Q2)"],
    "follow_up_items": [{"question_number": 2, "topic": "Exceptions", "reason": "Incorrect answer"}],
    "coaching_actions": ["Pair on exception handling exercises"],
    "practice_topics": ["Exceptions"],
    "evidence_sufficiency": "limited",
    "evidence_explanation": "Only two questions were asked.",
    "limitations": None,
}
GROUP_JSON = {
    "overview": "Mixed results across the group.",
    "common_strengths": ["Basics"],
    "common_gaps": ["Exceptions"],
    "training_priorities": ["Exception handling workshop"],
    "candidates_needing_support": [{"candidate_ref": "C1", "evidence": "Below 50% on Exceptions"}],
    "limitations": "Small group.",
}
TEXT_GRADE_JSON = {"awarded_points": 3, "rationale": "Covers 3 of 4 rubric points.", "confidence": "high", "needs_review": False}


def classify_prompt(system: str) -> str:
    if "for a student" in system:
        return "student"
    if "coaching report" in system:
        return "admin"
    if "group performance" in system:
        return "group"
    if "grading one written answer" in system:
        return "text_grade"
    return "unknown"


class ScriptedChatModel(BaseChatModel):
    """Deterministic chat model: `handler(kind, system, human)` returns the reply text."""

    handler: Any
    calls: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted-test-model"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        system = str(messages[0].content)
        human = str(messages[-1].content)
        kind = classify_prompt(system)
        self.calls.append({"kind": kind, "system": system, "human": human, "kwargs": kwargs})
        reply = self.handler(kind, system, human)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])


class FakeAI:
    def __init__(self) -> None:
        self.calls: list = []
        self.responses: dict[str, Callable[[], str]] = {}
        self.reset()

    def reset(self) -> None:
        self.calls.clear()
        self.responses = {
            "student": lambda: json.dumps(STUDENT_JSON),
            "admin": lambda: json.dumps(ADMIN_JSON),
            "group": lambda: json.dumps(GROUP_JSON),
            "text_grade": lambda: json.dumps(TEXT_GRADE_JSON),
        }

    def set(self, kind: str, response: str | Callable[[], str] | Exception) -> None:
        if isinstance(response, Exception):
            def raiser(exc=response):
                raise exc
            self.responses[kind] = raiser
        elif callable(response):
            self.responses[kind] = response
        else:
            self.responses[kind] = lambda r=response: r

    def handler(self, kind: str, system: str, human: str) -> str:
        return self.responses.get(kind, lambda: "{}")()

    def calls_of(self, kind: str) -> list:
        return [c for c in self.calls if c["kind"] == kind]


@pytest.fixture(autouse=True)
def fake_ai():
    fake = FakeAI()

    def factory():
        model = ScriptedChatModel(handler=fake.handler)
        model.calls = fake.calls
        return model

    ai_service.use_llm_factory(factory)
    yield fake
    ai_service.use_llm_factory(None)


# ---------------------------------------------------------------------------
# HTTP clients
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def make_client():
    """Factory for independent clients (each has its own cookie jar = one browser)."""
    return lambda: TestClient(app)


@pytest.fixture
def settings():
    return get_settings()
