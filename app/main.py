"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.api import deps
from app.api import (
    auth,
    cameras,
    downloads,
    employees,
    equipment,
    events,
    maintenance,
    pages,
    reports,
    settings as settings_api,
    sheets_setup,
    training,
)
from app.config import get_settings
from app.database import init_db
from app.services.monitoring_service import get_monitor
from app.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    logger.info("Ishga tushirilmoqda: %s (%s)", settings.env, settings.timezone)
    for warning in settings.warnings():
        logger.warning("Konfiguratsiya: %s", warning)

    init_db(settings)
    monitor = get_monitor(settings)
    monitor.start()
    logger.info("Tizim tayyor: http://%s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        monitor.stop()
        logger.info("Tizim to‘xtatildi")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI CCTV inventar tizimi",
        description="Xodimlarni tanish va jihozlar harakati monitoringi",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="cctv_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.is_production,
    )

    if deps.STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(deps.STATIC_DIR)), name="static")

    for module in (
        pages,
        auth,
        employees,
        equipment,
        cameras,
        events,
        maintenance,
        reports,
        settings_api,
        downloads,
        sheets_setup,
        training,
    ):
        app.include_router(module.router)

    # -- error handling (no stack traces or internals leaked to the browser) --
    @app.exception_handler(deps.AuthRequired)
    async def auth_required_handler(request: Request, exc: deps.AuthRequired):
        return RedirectResponse(url=f"/login?next={exc.next_url}", status_code=303)

    @app.exception_handler(deps.Forbidden)
    async def forbidden_handler(request: Request, exc: deps.Forbidden):
        return deps.templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "title": "Ruxsat yo‘q", "message": exc.message, "user": None},
            status_code=403,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        message = str(exc.detail) if exc.status_code < 500 else "Server xatosi."
        return deps.templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "title": f"Xatolik {exc.status_code}", "message": message, "user": None},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        logger.warning("Validatsiya xatosi: %s %s", request.url.path, exc.errors()[:3])
        return JSONResponse(
            {"detail": "Kiritilgan ma’lumotlar noto‘g‘ri.", "errors": exc.errors()[:5]},
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):  # pragma: no cover - safety net
        logger.exception("Kutilmagan xatolik: %s", request.url.path)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Ichki server xatosi."}, status_code=500)
        return deps.templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "title": "Server xatosi", "message": "Batafsil ma’lumot log faylida.", "user": None},
            status_code=500,
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        monitor = get_monitor()
        return {
            "status": "ok",
            "monitoring": monitor.is_started,
            "cameras_running": sum(1 for item in monitor.health() if item.get("running")),
        }

    return app


app = create_app()
