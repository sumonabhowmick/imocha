"""Application configuration loaded from environment variables (and an optional .env file).

All secrets come from the environment. Settings are validated once at startup so that
misconfiguration fails fast with a clear message instead of failing later at runtime.
"""
from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_SECRET = "replace_with_a_secure_random_secret"

# Default performance categories (project-defined labels, not an external standard).
# min is inclusive, max is exclusive, except the final band which includes 100.
DEFAULT_CATEGORY_SCALE = [
    {"label": "Adaptive", "min": 0, "max": 50},
    {"label": "Developer", "min": 50, "max": 75},
    {"label": "Competitive", "min": 75, "max": 100},
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core ---
    app_env: str = Field("development", pattern="^(development|test|production)$")
    app_secret_key: str = ""
    app_timezone: str = "Asia/Kolkata"
    # Public URL students use to reach the backend (used to build email links).
    public_base_url: str = "http://localhost:8000"
    # URL of the Streamlit dashboard (used in admin notification emails).
    dashboard_url: str = "http://localhost:8501"
    # Comma-separated list of allowed CORS origins. Empty = no cross-origin access.
    cors_origins: str = ""

    # --- Database ---
    database_url: str = ""

    # --- OpenAI ---
    openai_api_key: str = ""
    openai_model: str = ""
    openai_timeout_seconds: float = 60.0
    ai_text_grading_enabled: bool = False

    # --- Email ---
    # "smtp" sends real email. "outbox" (development only) writes emails to files in
    # EMAIL_OUTBOX_DIR instead of sending them, so the flow can be demonstrated locally.
    email_backend: str = Field("smtp", pattern="^(smtp|outbox)$")
    email_outbox_dir: str = "outbox"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_ssl: bool = False  # True for implicit TLS (port 465); otherwise STARTTLS
    smtp_timeout_seconds: float = 20.0
    admin_notification_email: str = ""  # Falls back to the assessment creator's email
    support_contact: str = "your assessment administrator"

    # --- Invitations / passcodes ---
    invitation_ttl_minutes: int = Field(1440, ge=5)
    passcode_ttl_minutes: int = Field(10, ge=1, le=60)
    passcode_length: int = Field(6, ge=6, le=10)
    passcode_max_attempts: int = Field(5, ge=1, le=20)
    passcode_lockout_minutes: int = Field(15, ge=1)
    passcode_resend_cooldown_seconds: int = Field(60, ge=0)
    passcode_max_resends: int = Field(5, ge=0)

    # --- Sessions ---
    admin_session_ttl_minutes: int = Field(480, ge=5)
    student_session_ttl_minutes: int = Field(180, ge=5)
    login_max_attempts: int = Field(5, ge=1)
    login_window_seconds: int = Field(300, ge=10)

    # --- Assessments ---
    assessment_default_duration_minutes: int = Field(45, ge=1)
    assessment_completion_policy: str = Field(
        "finish_after_window", pattern="^(finish_after_window|hard_end_at_window)$"
    )
    # Background sweeper that auto-finalizes expired attempts (seconds; 0 disables it).
    sweeper_interval_seconds: int = Field(60, ge=0)

    demo_mode: bool = False

    @field_validator("app_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"APP_TIMEZONE '{value}' is not a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def _validate_required(self) -> "Settings":
        problems = []
        if not self.app_secret_key or self.app_secret_key == PLACEHOLDER_SECRET:
            problems.append(
                "APP_SECRET_KEY is missing. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        elif len(self.app_secret_key) < 32:
            problems.append("APP_SECRET_KEY must be at least 32 characters long.")
        if not self.database_url:
            problems.append("DATABASE_URL is missing (PostgreSQL connection string).")
        elif not self.database_url.startswith("postgresql"):
            problems.append("DATABASE_URL must point to PostgreSQL (postgresql+psycopg://...).")
        if self.email_backend == "outbox" and self.app_env == "production":
            problems.append("EMAIL_BACKEND=outbox is for development only; use smtp in production.")
        if problems:
            raise ValueError("Invalid configuration:\n  - " + "\n  - ".join(problems))
        return self

    # --- Convenience helpers ---
    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def ai_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_model)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from_email)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
