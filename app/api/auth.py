"""Login, logout and user administration."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import deps
from app.database import get_db
from app.models import User
from app.models.enums import Role
from app.services import audit_service
from app.utils.security import (
    SESSION_CSRF_KEY,
    SESSION_ROLE_KEY,
    SESSION_USER_KEY,
    hash_password,
    safe_next_url,
    verify_password,
)
from app.utils.time_utils import utc_now

logger = logging.getLogger(__name__)
router = APIRouter()

# Very small in-process brute-force guard (per username + client host).
_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 300
_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


def _blocked(key: str) -> bool:
    now = time.time()
    with _attempts_lock:
        stamps = [stamp for stamp in _attempts.get(key, []) if now - stamp < _WINDOW_SECONDS]
        _attempts[key] = stamps
        return len(stamps) >= _MAX_ATTEMPTS


def _register_failure(key: str) -> None:
    with _attempts_lock:
        _attempts.setdefault(key, []).append(time.time())


def _clear(key: str) -> None:
    with _attempts_lock:
        _attempts.pop(key, None)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if deps.current_user(request, db) is not None:
        return RedirectResponse(url="/", status_code=303)
    return deps.render(request, "login.html", {"next": safe_next_url(next), "error": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    deps.verify_csrf(request, csrf_token)
    target = safe_next_url(next)
    username = username.strip().lower()
    client = request.client.host if request.client else "?"
    key = f"{username}|{client}"

    if _blocked(key):
        audit_service.record(db, action="auth.blocked", entity="user", entity_id=username, username=username)
        db.commit()
        return deps.render(
            request,
            "login.html",
            {"next": target, "error": "Juda ko‘p urinish. 5 daqiqadan keyin qayta urinib ko‘ring."},
            status_code=429,
        )

    user = db.scalar(select(User).where(func.lower(User.username) == username))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        _register_failure(key)
        audit_service.record(db, action="auth.failed", entity="user", entity_id=username, username=username)
        db.commit()
        return deps.render(
            request,
            "login.html",
            {"next": target, "error": "Login yoki parol noto‘g‘ri."},
            status_code=401,
        )

    _clear(key)
    # Clear the old session (basic session-fixation protection) and issue a new
    # CSRF token for the authenticated session.
    request.session.clear()
    request.session[SESSION_USER_KEY] = user.id
    request.session[SESSION_ROLE_KEY] = user.role
    deps.ensure_csrf_token(request)

    user.last_login_at = utc_now()
    audit_service.record(db, action="auth.login", entity="user", entity_id=user.username, user=user)
    db.commit()
    logger.info("Foydalanuvchi kirdi: %s (%s)", user.username, user.role)
    return RedirectResponse(url=target, status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: Optional[str] = Form(None)):
    if deps.csrf_valid(request.session.get(SESSION_CSRF_KEY), csrf_token):
        request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ---------------------------------------------------------------------------
# User administration (administrator only)
# ---------------------------------------------------------------------------
@router.post("/api/users")
def create_user(
    request: Request,
    username: str = Form(...),
    full_name: str = Form(""),
    password: str = Form(...),
    role: str = Form(Role.VIEWER.value),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    username = username.strip().lower()
    if len(username) < 3 or len(password) < 8:
        deps.flash(request, "Login kamida 3, parol kamida 8 belgidan iborat bo‘lishi kerak.", "error")
        return deps.redirect("/settings#foydalanuvchilar")
    if db.scalar(select(User).where(func.lower(User.username) == username)):
        deps.flash(request, "Bu login allaqachon mavjud.", "error")
        return deps.redirect("/settings#foydalanuvchilar")
    try:
        role_value = Role(role).value
    except ValueError:
        deps.flash(request, "Rol noto‘g‘ri.", "error")
        return deps.redirect("/settings#foydalanuvchilar")

    user = User(
        username=username,
        full_name=full_name.strip()[:160],
        password_hash=hash_password(password),
        role=role_value,
        is_active=True,
    )
    db.add(user)
    db.flush()
    audit_service.record(
        db, action="user.create", entity="user", entity_id=user.username, details={"role": role_value}, user=admin
    )
    db.commit()
    deps.flash(request, f"Foydalanuvchi qo‘shildi: {user.username}")
    return deps.redirect("/settings#foydalanuvchilar")


@router.post("/api/users/{user_id}/password")
def change_password(
    request: Request,
    user_id: int,
    password: str = Form(...),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    actor = deps.require_user(request, db)
    deps.verify_csrf(request, csrf_token)
    user = db.get(User, user_id)
    if user is None:
        deps.flash(request, "Foydalanuvchi topilmadi.", "error")
        return deps.redirect("/settings#foydalanuvchilar")
    if not actor.is_admin and actor.id != user.id:
        raise deps.Forbidden("Faqat o‘z parolingizni o‘zgartira olasiz.")
    if len(password) < 8:
        deps.flash(request, "Parol kamida 8 belgidan iborat bo‘lishi kerak.", "error")
        return deps.redirect("/settings#foydalanuvchilar")
    user.password_hash = hash_password(password)
    audit_service.record(db, action="user.password", entity="user", entity_id=user.username, user=actor)
    db.commit()
    deps.flash(request, "Parol yangilandi.")
    return deps.redirect("/settings#foydalanuvchilar")


@router.post("/api/users/{user_id}/toggle")
def toggle_user(
    request: Request,
    user_id: int,
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    user = db.get(User, user_id)
    if user is None:
        deps.flash(request, "Foydalanuvchi topilmadi.", "error")
        return deps.redirect("/settings#foydalanuvchilar")
    if user.id == admin.id:
        deps.flash(request, "O‘zingizni o‘chirib qo‘ya olmaysiz.", "error")
        return deps.redirect("/settings#foydalanuvchilar")
    user.is_active = not user.is_active
    audit_service.record(
        db, action="user.toggle", entity="user", entity_id=user.username, details={"active": user.is_active}, user=admin
    )
    db.commit()
    deps.flash(request, f"{user.username}: {'faollashtirildi' if user.is_active else 'bloklandi'}.")
    return deps.redirect("/settings#foydalanuvchilar")
