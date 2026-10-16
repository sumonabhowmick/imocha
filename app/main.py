"""AssessFlow AI - FastAPI application entry point.

Run with:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from app.api import assessments, auth, candidates, feedback, invitations, questions, results, student_assessment
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


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith("/docs"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
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
for module in (auth, assessments, questions, candidates, invitations, student_assessment, results, feedback):
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
    }


# --- Student web pages (plain HTML + JS served by FastAPI; no external quiz platform) ---
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/start")


@app.get("/start", include_in_schema=False)
def start_page():
    return FileResponse(STATIC_DIR / "start.html", headers={"Cache-Control": "no-store"})


@app.get("/assessment", include_in_schema=False)
def assessment_page():
    return FileResponse(STATIC_DIR / "assessment.html", headers={"Cache-Control": "no-store"})
