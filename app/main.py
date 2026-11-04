"""AssessFlow AI - FastAPI application entry point.

Run with:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
import mimetypes

# Windows can map .js to text/plain in the registry, which breaks ES modules.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/wasm", ".wasm")

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from app.api import (
    agents,
    assessments,
    auth,
    bank,
    batches,
    candidates,
    feedback,
    invitations,
    portal,
    questions,
    reports,
    results,
    student_assessment,
)
from app.config import get_settings
from app.database import SessionLocal
from app.errors import AppError
from app.workflows.assessment_workflow import Sweeper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("assessflow")

STATIC_DIR = Path(__file__).resolve().parent / "static"
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail fast with a clear message if PostgreSQL is unreachable or not migrated.
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1 FROM alembic_version"))
    except OperationalError:
        logger.error(
            "Cannot connect to PostgreSQL. Check that the server is running and DATABASE_URL in .env is correct."
        )
    except SQLAlchemyError:
        logger.error("Database schema is missing. Run:  alembic upgrade head")
    sweeper = Sweeper(settings.sweeper_interval_seconds)
    sweeper.start()
    if settings.demo_mode:
        logger.warning("DEMO_MODE is enabled: demonstration records are labelled [DEMO].")
    yield
    sweeper.stop()


app = FastAPI(
    title="AssessFlow AI",
    version="1.0.0",
    lifespan=lifespan,
    # Interactive API docs only outside production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
    )


# Only answer requests addressed to known host names (blocks Host-header tricks).
_hosts = {h.strip() for h in settings.allowed_hosts.split(",") if h.strip()}
for _url in (settings.public_base_url, settings.dashboard_url):
    _host = urlparse(_url).hostname
    if _host:
        _hosts.add(_host)
# Render sets this automatically to the service's own address (e.g. assessflow.onrender.com).
if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
    _hosts.add(os.environ["RENDER_EXTERNAL_HOSTNAME"])
if _hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(_hosts))


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Reject oversized request bodies early (large uploads cannot exhaust the server).
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.max_request_bytes:
        return JSONResponse(status_code=413, content={"detail": "The request is too large.", "code": "too_large"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    # The camera is only usable by our own pages (for proctoring); everything else is off.
    response.headers["Permissions-Policy"] = (
        "camera=(self), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if not request.url.path.startswith("/docs"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data: blob:; "
            "media-src 'self' blob:; worker-src 'self' blob:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# --- Error handling: safe messages only, never stack traces or SQL --------------------
@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    # Echo field locations and messages, but never the submitted values (could be a passcode).
    errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "Invalid value")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Invalid request.", "errors": errors, "code": "validation_error"})


@app.exception_handler(OperationalError)
async def db_unavailable_handler(_request: Request, _exc: OperationalError):
    logger.error("Database operation failed (is PostgreSQL running?)")
    return JSONResponse(status_code=503, content={"detail": "The database is temporarily unavailable.", "code": "db_unavailable"})


@app.exception_handler(IntegrityError)
async def integrity_handler(_request: Request, _exc: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "The request conflicts with existing data.", "code": "conflict"})


# --- Routers ------------------------------------------------------------------------
for module in (
    auth, assessments, questions, candidates, invitations, student_assessment, results, feedback, agents,
    batches, bank, portal, reports,
):
    app.include_router(module.router)


@app.get("/api/health", tags=["health"])
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        database = "ok"
    except SQLAlchemyError:
        database = "unavailable"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "ai_configured": settings.ai_configured,
        "email_backend": settings.email_backend,
        "smtp_configured": settings.smtp_configured,
        "demo_mode": settings.demo_mode,
        "timezone": settings.app_timezone,
    }


# --- Web app (admin console + student portal; plain HTML/JS served by FastAPI) ---------
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
APP_INDEX = STATIC_DIR / "app" / "index.html"


def _spa():
    return FileResponse(APP_INDEX, headers={"Cache-Control": "no-store"})


# Every entry point serves the same single-page app; it routes to the right screen.
for _path in ("/", "/login", "/admin", "/portal", "/start", "/assessment"):
    app.add_api_route(_path, _spa, methods=["GET"], include_in_schema=False)
