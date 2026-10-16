"""Small shared UI helpers for the dashboard pages."""
from __future__ import annotations

import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

load_dotenv()
TZ_NAME = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
TZ = ZoneInfo(TZ_NAME)


def fmt_dt(value: str | None) -> str:
    """Format an ISO timestamp from the API in the application timezone."""
    if not value:
        return "-"
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(TZ).strftime("%d %b %Y, %I:%M %p")


def to_local(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ)


def combine_local(d: date, t: time) -> str:
    """Date + time picked in the app timezone -> timezone-aware ISO string."""
    return datetime.combine(d, t).replace(tzinfo=TZ).isoformat()


def fmt_pct(value) -> str:
    return "-" if value is None else f"{value:.2f}%"


def bullet_list(title: str, items: list | None) -> None:
    if items:
        st.markdown(f"**{title}**")
        for item in items:
            st.markdown(f"- {item}")


STATUS_ICONS = {
    "draft": "📝",
    "scheduled": "🗓️",
    "active": "🟢",
    "completed": "✅",
    "archived": "🗄️",
    "sent": "✅",
    "outbox": "📁",
    "failed": "❌",
    "queued": "⏳",
    "pending": "⏳",
    "final": "✅",
    "provisional": "🔎",
}


def badge(status: str | None) -> str:
    if not status:
        return "-"
    return f"{STATUS_ICONS.get(status, '')} {status}".strip()
