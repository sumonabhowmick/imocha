"""Helpers shared by the LangGraph graphs.

`graph_node` wraps every node so that:
* it gets its own short-lived database session (parallel nodes run in threads),
* each executed step is appended to the state's `steps` trace,
* an unexpected exception is recorded in `errors` (with a safe message) instead of
  crashing the worker; the routing functions then stop the graph at END.
"""
from __future__ import annotations

import functools
import logging
from typing import Callable

from langgraph.graph import END

from app.database import SessionLocal
from app.errors import AppError

logger = logging.getLogger(__name__)


def graph_node(name: str) -> Callable:
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(state: dict) -> dict:
            db = SessionLocal()
            try:
                update = fn(state, db) or {}
            except AppError as exc:
                db.rollback()
                update = {"errors": [f"{name}: {exc.message}"]}
            except Exception as exc:  # keep saved data; report a safe error
                db.rollback()
                logger.exception("Workflow node '%s' failed", name)
                update = {"errors": [f"{name}: unexpected {type(exc).__name__}"]}
            finally:
                db.close()
            update["steps"] = [name]
            return update

        return wrapper

    return decorator


def go_to(target: str) -> Callable[[dict], str]:
    """Routing function: continue to `target` unless an earlier node recorded an error."""

    def route(state: dict) -> str:
        return END if state.get("errors") else target

    route.__name__ = f"to_{target}"
    return route
