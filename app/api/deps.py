"""Shared FastAPI dependencies: sessions, authorisation, CSRF, rendering."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, get_settings
from app.models import User
from app.models.enums import (
    AssetMovementType,
    AssignmentKind,
    Direction,
    EquipmentStatus,
    EventType,
    RepairStatus,
    Role,
    SyncStatus,
    TransactionType,
    UsageAction,
    VerificationStatus,
)
from app.utils.security import SESSION_CSRF_KEY, SESSION_NOTICE_KEY, SESSION_USER_KEY, csrf_valid, new_csrf_token
from app.utils.time_utils import format_local, format_local_short, local_today, workplace_timezone

logger = logging.getLogger(__name__)

TEMPLATES_DIR = PROJECT_ROOT / "app" / "ui" / "templates"
STATIC_DIR = PROJECT_ROOT / "app" / "ui" / "static"


class AuthRequired(Exception):
    """Raised when an unauthenticated visitor hits a protected page."""

    def __init__(self, next_url: str = "/") -> None:
        self.next_url = next_url
        super().__init__("Authentication required")


class Forbidden(Exception):
    """Raised when the authenticated user lacks the required role."""

    def __init__(self, message: str = "Bu amal uchun ruxsat yo‘q.") -> None:
        self.message = message
        super().__init__(message)


def bar_style(value: Optional[float]) -> Markup:
    """Inline width for the ``.bar span`` charts, clamped to 0–100 %.

    The whole attribute is rendered from Python so templates never contain
    ``style="{{ … }}"``: that keeps the CSS language server quiet and makes the
    clamp (bad/None input, >100 %) explicit instead of producing broken CSS.
    """
    try:
        percentage = max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        percentage = 0.0
    return Markup(f'style="width: {percentage:.1f}%"')


def _build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["local_dt"] = format_local
    templates.env.filters["local_short"] = format_local_short
    templates.env.filters["pct"] = lambda value: f"{float(value) * 100:.0f}%" if value is not None else "—"
    templates.env.filters["score"] = lambda value: f"{float(value):.2f}" if value is not None else "—"
    templates.env.globals["bar_style"] = bar_style
    templates.env.globals.update(
        {
            "EventType": EventType,
            "VerificationStatus": VerificationStatus,
            "Direction": Direction,
            "Role": Role,
            "TransactionType": TransactionType,
            "EquipmentStatus": EquipmentStatus,
            "SyncStatus": SyncStatus,
            "AssignmentKind": AssignmentKind,
            "RepairStatus": RepairStatus,
            "AssetMovementType": AssetMovementType,
            "UsageAction": UsageAction,
            "today": local_today,
        }
    )
    return templates


templates = _build_templates()


def get_csrf_token(request: Request) -> str:
    token = request.session.get(SESSION_CSRF_KEY)
    if not token:
        token = new_csrf_token()
        request.session[SESSION_CSRF_KEY] = token
    return token


def ensure_csrf_token(request: Request) -> None:
    get_csrf_token(request)


def verify_csrf(request: Request, submitted: Optional[str]) -> None:
    if not csrf_valid(request.session.get(SESSION_CSRF_KEY), submitted):
        logger.warning("CSRF tekshiruvi muvaffaqiyatsiz: %s %s", request.method, request.url.path)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSRF token noto‘g‘ri yoki muddati o‘tgan.")


def current_user(request: Request, db: Session) -> Optional[User]:
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    user = db.get(User, int(user_id))
    if user is None or not user.is_active:
        request.session.pop(SESSION_USER_KEY, None)
        return None
    return user


def require_user(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if user is None:
        raise AuthRequired(request.url.path)
    return user


def require_roles(request: Request, db: Session, *roles: Role) -> User:
    user = require_user(request, db)
    if user.role_enum not in roles:
        raise Forbidden()
    return user


def require_admin(request: Request, db: Session) -> User:
    return require_roles(request, db, Role.ADMIN)


def require_operator(request: Request, db: Session) -> User:
    return require_roles(request, db, Role.ADMIN, Role.OPERATOR)


def flash(request: Request, message: str, level: str = "ok") -> None:
    notices = request.session.get(SESSION_NOTICE_KEY) or []
    notices.append({"message": message, "level": level})
    request.session[SESSION_NOTICE_KEY] = notices[-6:]


def consume_notices(request: Request) -> list[dict]:
    return request.session.pop(SESSION_NOTICE_KEY, []) or []


def render(
    request: Request,
    template: str,
    context: Optional[dict[str, Any]] = None,
    *,
    user: Optional[User] = None,
    status_code: int = 200,
) -> HTMLResponse:
    settings = get_settings()
    base = {
        "request": request,
        "user": user,
        "csrf_token": get_csrf_token(request),
        "notices": consume_notices(request),
        "app_name": "AI CCTV inventar tizimi",
        "settings": settings,
        "workplace_tz": str(workplace_timezone()),
        "today": local_today(),
        "now": datetime.now(workplace_timezone()),
        "is_admin": bool(user and user.is_admin),
        "is_operator": bool(user and user.role_enum in {Role.ADMIN, Role.OPERATOR}),
        "is_technician": bool(user and user.role_enum == Role.TECHNICIAN),
        # Nav badge next to "Tekshirish kerak" (base.html). Routes that know the
        # real queue length override it; the others simply show no badge.
        "review_pending": 0,
    }
    if context:
        base.update(context)
    return templates.TemplateResponse(request, template, base, status_code=status_code)


def safe_range(start_raw: Optional[str], end_raw: Optional[str]) -> tuple[Optional[datetime], Optional[datetime]]:
    """Parse yyyy-mm-dd date inputs into UTC datetimes."""
    from datetime import time

    from app.utils.time_utils import as_utc

    start = end = None
    try:
        if start_raw:
            start = as_utc(datetime.combine(date.fromisoformat(start_raw), time.min, tzinfo=workplace_timezone()))
        if end_raw:
            end = as_utc(datetime.combine(date.fromisoformat(end_raw), time.max.replace(microsecond=0), tzinfo=workplace_timezone()))
    except ValueError:
        return None, None
    return start, end


def form_int(raw: Optional[str], label: str = "Qiymat") -> Optional[int]:
    """Parse an optional integer form field.

    HTML number inputs post an empty string when the user leaves them blank, so
    an empty value means "not set" instead of a validation error.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise ValueError(f"{label} butun son bo‘lishi kerak.") from exc


def form_float(raw: Optional[str], label: str = "Qiymat") -> Optional[float]:
    """Parse an optional decimal form field (empty string -> None)."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"{label} son bo‘lishi kerak.") from exc


def redirect(url: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status_code)


def evidence_file(relative: str) -> Path:
    from app.services.evidence_service import resolve_evidence

    return resolve_evidence(relative)
