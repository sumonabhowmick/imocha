"""Shared plumbing for agent tools.

Every agent tool is a thin wrapper around an EXISTING service function (the same code
the dashboard buttons use), so all existing validation and security rules still apply.

Risk levels
-----------
* "read"     - looks data up; runs immediately.
* "write"    - safe change (e.g. a draft quiz, a candidate record); runs immediately and
               is written to the audit log.
* "approval" - risky change (publish, archive, send email, re-run AI); the agent PAUSES
               with LangGraph's interrupt() until an admin approves or rejects it in the
               dashboard. Only then does the action run (and get audited).

Actions that change scores, delete data, reveal passcodes or create admins are never
offered as tools.
"""
from __future__ import annotations

import inspect
import json
import logging
import typing
from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.errors import AppError
from app.models import User

audit_log = logging.getLogger("assessflow.agents.audit")
logger = logging.getLogger(__name__)

MAX_TOOL_OUTPUT_CHARS = 6000

# tool name -> risk level, filled in by @agent_tool (used by the API, dashboard and tests)
TOOL_RISK: dict[str, str] = {}


class ToolInputError(Exception):
    """A problem with the agent's tool input; the message is returned to the agent."""


def mask_email(address: str | None) -> str | None:
    """'asha@example.com' -> 'as***@example.com' (enough to recognise, not to copy)."""
    if not address or "@" not in address:
        return address
    local, domain = address.split("@", 1)
    return f"{local[:2]}***@{domain}"


def to_text(data: Any) -> str:
    """Compact JSON for the model, capped so one tool cannot flood the context window."""
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[:MAX_TOOL_OUTPUT_CHARS] + ' ..."[truncated]"'
    return text


def admin_from_config(db: Session, config: RunnableConfig | None) -> User:
    """The admin who is chatting with the assistant (set by the API, never by the model)."""
    admin_id = ((config or {}).get("configurable") or {}).get("admin_id")
    admin = db.get(User, admin_id) if admin_id else None
    if admin is None or not admin.is_active:
        raise ToolInputError("No authorised admin is attached to this conversation.")
    return admin


def request_approval(action: str, summary: str, details: dict) -> dict:
    """Pause the whole multi-agent graph until an admin decides.

    LangGraph saves the graph's state; when the admin answers, the graph resumes and
    this call returns their decision: {"approved": bool, "note": str}."""
    decision = interrupt({"action": action, "summary": summary, "details": details})
    if not isinstance(decision, dict):
        return {"approved": False, "note": "Invalid decision."}
    return decision


def agent_tool(name: str, risk: str) -> Callable:
    """Decorator for the body of an agent tool.

    The wrapped function receives (db, config, **tool_args) and returns any
    JSON-serialisable value. Errors from the existing services are returned to the agent
    as text so it can explain or correct itself, instead of crashing the conversation.
    """
    assert risk in ("read", "write", "approval")

    def decorator(fn: Callable) -> Callable:
        TOOL_RISK[name] = risk

        def run(config: RunnableConfig | None = None, **kwargs) -> str:
            admin_id = ((config or {}).get("configurable") or {}).get("admin_id")
            db = SessionLocal()
            try:
                result = fn(db, config, **kwargs)
                if risk != "read":
                    audit_log.info("agent tool=%s risk=%s admin=%s args=%s outcome=ok", name, risk, admin_id, _safe_args(kwargs))
                return to_text(result)
            except GraphBubbleUp:
                raise  # LangGraph's interrupt() for approvals - must propagate
            except (AppError, ToolInputError, ValueError) as exc:
                db.rollback()
                message = exc.message if isinstance(exc, AppError) else str(exc)
                if risk != "read":
                    audit_log.info("agent tool=%s risk=%s admin=%s args=%s outcome=error", name, risk, admin_id, _safe_args(kwargs))
                return to_text({"error": message[:500]})
            except Exception as exc:  # unexpected: never leak internals to the model
                db.rollback()
                logger.exception("Agent tool %s failed", name)
                return to_text({"error": f"The tool failed unexpectedly ({type(exc).__name__})."})
            finally:
                db.close()

        # Give LangChain the tool's real arguments (without `db`), plus the injected
        # `config`, so it can build the input schema the model sees.
        hints = typing.get_type_hints(fn)
        params = [p.replace(annotation=hints.get(n, Any)) for n, p in inspect.signature(fn).parameters.items() if n not in ("db", "config")]
        params.append(inspect.Parameter("config", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=RunnableConfig))
        run.__signature__ = inspect.Signature(params, return_annotation=str)
        run.__annotations__ = {p.name: p.annotation for p in params} | {"return": str}
        run.__name__ = name
        return StructuredTool.from_function(func=run, name=name, description=inspect.getdoc(fn) or name)

    return decorator


def _safe_args(kwargs: dict) -> dict:
    """Arguments for the audit log, with email addresses masked."""
    return {k: (mask_email(v) if isinstance(v, str) and "@" in v else v) for k, v in kwargs.items()}
