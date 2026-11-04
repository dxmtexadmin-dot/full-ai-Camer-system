"""Admin-only data maintenance page: delete stills and junk events.

Only administrators can reach these routes. Every action is CSRF-protected,
requires the confirmation word and is written to the audit log. The inventory
ledger is never touched: confirmed events and events the ledger points at are
refused by :mod:`app.services.cleanup_service`.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api import deps
from app.database import get_db
from app.models import Event
from app.models.enums import EventType, VerificationStatus
from app.services import cleanup_service
from app.utils.time_utils import utc_now

logger = logging.getLogger(__name__)
router = APIRouter()

# The operator types this word before anything is deleted.
CONFIRM_WORD = "OCHIRISH"

# Groups shown in the bulk form; camera chatter is the usual junk.
PURGE_GROUPS: dict[str, list[str]] = {
    "camera": [EventType.CAMERA_DISCONNECTED.value, EventType.CAMERA_RECONNECTED.value],
    "person": [
        EventType.PERSON_ENTERED.value,
        EventType.PERSON_EXITED.value,
        EventType.UNKNOWN_PERSON_ENTERED.value,
        EventType.UNKNOWN_PERSON_EXITED.value,
        EventType.UNKNOWN_PERSON_DETECTED.value,
    ],
    "equipment": [
        EventType.EQUIPMENT_BROUGHT_IN.value,
        EventType.EQUIPMENT_TAKEN_OUT.value,
        EventType.EQUIPMENT_RETURNED.value,
    ],
    "possession": [
        EventType.EQUIPMENT_PICKED_UP.value,
        EventType.EQUIPMENT_PUT_DOWN.value,
    ],
}

GROUP_LABELS = {
    "camera": "Kamera uzilish/tiklanish",
    "person": "Odam kirish/chiqish",
    "equipment": "Jihoz kirim/chiqim",
    "possession": "Jihoz olindi/qo‘yildi",
}


@router.get("/data", response_class=HTMLResponse)
def data_page(request: Request, db: Session = Depends(get_db)):
    user = deps.require_admin(request, db)
    stats = cleanup_service.storage_stats(db)
    return deps.render(
        request,
        "data.html",
        {
            "stats": stats,
            "stats_human": {
                "evidence": cleanup_service.human_bytes(stats["evidence_bytes"]),
                "db": cleanup_service.human_bytes(stats["db_bytes"]),
            },
            "purge_groups": [(key, GROUP_LABELS[key]) for key in PURGE_GROUPS],
            "statuses": [VerificationStatus.PENDING, VerificationStatus.REJECTED],
            "confirm_word": CONFIRM_WORD,
        },
        user=user,
    )


def _confirmed(raw: str) -> bool:
    return (raw or "").strip().upper() == CONFIRM_WORD


def _back(request: Request, next_url: str) -> RedirectResponse:
    return deps.redirect(next_url or "/data")


@router.post("/api/events/{event_id}/delete")
def delete_event_action(
    request: Request,
    event_id: int,
    reason: str = Form(""),
    next_url: str = Form("/data"),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    event = db.get(Event, event_id)
    if event is None:
        deps.flash(request, "Hodisa topilmadi.", "error")
        return _back(request, next_url)
    try:
        public_id = cleanup_service.delete_event(db, event, user=user, reason=reason)
        db.commit()
    except cleanup_service.CleanupError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return _back(request, next_url)
    deps.flash(request, f"{public_id} o‘chirildi. Amal audit jurnaliga yozildi.")
    return _back(request, next_url)


@router.post("/api/events/{event_id}/evidence/delete")
def delete_evidence_action(
    request: Request,
    event_id: int,
    next_url: str = Form("/data"),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    event = db.get(Event, event_id)
    if event is None:
        deps.flash(request, "Hodisa topilmadi.", "error")
        return _back(request, next_url)
    if not event.evidence_path:
        deps.flash(request, f"{event.public_id} uchun dalil rasmi yo‘q.", "error")
        return _back(request, next_url)
    public_id = event.public_id
    removed = cleanup_service.delete_evidence(db, event, user=user)
    db.commit()
    if removed:
        deps.flash(request, f"{public_id} dalil rasmi o‘chirildi.")
    else:
        deps.flash(request, f"{public_id}: fayl diskda topilmadi, yozuv tozalandi.", "error")
    return _back(request, next_url)


@router.post("/api/data/evidence/purge")
def purge_evidence_action(
    request: Request,
    days: str = Form("30"),
    confirm: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    if not _confirmed(confirm):
        deps.flash(request, f"Tasdiqlash so‘zi noto‘g‘ri: {CONFIRM_WORD} deb yozing.", "error")
        return deps.redirect("/data")
    try:
        period = max(1, int(deps.form_int(days, "Kun") or 0))
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/data")
    removed = cleanup_service.purge_evidence(db, days=period, user=user)
    db.commit()
    deps.flash(request, f"{removed} dalil rasmi o‘chirildi ({period} kundan eski).")
    return deps.redirect("/data")


@router.post("/api/data/evidence/orphans")
def purge_orphan_evidence_action(
    request: Request,
    confirm: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    if not _confirmed(confirm):
        deps.flash(request, f"Tasdiqlash so‘zi noto‘g‘ri: {CONFIRM_WORD} deb yozing.", "error")
        return deps.redirect("/data")
    removed = cleanup_service.purge_orphan_evidence(db, user=user)
    db.commit()
    deps.flash(request, f"{removed} egasiz rasm o‘chirildi.")
    return deps.redirect("/data")


@router.post("/api/data/events/purge")
def purge_events_action(
    request: Request,
    group: str = Form("camera"),
    status: str = Form("REJECTED"),
    days: str = Form("7"),
    confirm: str = Form(""),
    reason: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    if not _confirmed(confirm):
        deps.flash(request, f"Tasdiqlash so‘zi noto‘g‘ri: {CONFIRM_WORD} deb yozing.", "error")
        return deps.redirect("/data")
    event_types = PURGE_GROUPS.get(group or "")
    if not event_types:
        deps.flash(request, "Toifalar guruhi noto‘g‘ri tanlangan.", "error")
        return deps.redirect("/data")
    try:
        target_status = VerificationStatus((status or "").upper())
    except ValueError:
        deps.flash(request, "Holat noto‘g‘ri tanlangan.", "error")
        return deps.redirect("/data")
    if target_status is VerificationStatus.CONFIRMED:
        deps.flash(request, "Tasdiqlangan hodisalar o‘chirilmaydi — bu jurnal izi.", "error")
        return deps.redirect("/data")
    try:
        period = max(1, int(deps.form_int(days, "Kun") or 0))
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/data")

    before = utc_now() - timedelta(days=period)
    result = cleanup_service.purge_events(
        db,
        event_types=event_types,
        statuses=[target_status.value],
        before=before,
        user=user,
        reason=reason or f"Ommaviy tozalash: {group}/{target_status.value}, {period} kundan eski",
    )
    db.commit()
    deps.flash(
        request,
        f"{result['removed']} hodisa o‘chirildi, {result['skipped']} himoyalangan, "
        f"{result['files']} dalil rasmi olib tashlandi.",
    )
    return deps.redirect("/data")
