"""Initial reference data: equipment categories, bootstrap admin, sample camera."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Camera, EquipmentCategory, Role, User
from app.models.enums import CameraStatus, SourceType
from app.utils.security import hash_password

logger = logging.getLogger(__name__)

# code, Uzbek label, YOLO class names (deployed model or COCO fallback), conf, order
DEFAULT_CATEGORIES: list[tuple[str, str, list[str], float, int, str]] = [
    ("person", "Shaxs", ["person"], 0.45, 0, "Odamlarni kuzatish uchun asosiy sinf."),
    ("desktop_pc", "Stol kompyuteri", ["desktop_pc", "desktop", "pc"], 0.60, 10, ""),
    (
        "monitor",
        "Monitor",
        ["monitor", "screen", "display", "tv", "tvmonitor"],
        0.60,
        20,
        "COCO modelida monitor sinfi yo‘q: u ekranni 'tv' deb ataydi. Shu sababli 'tv' yorlig‘i "
        "Monitor toifasiga bog‘langan (tekshirilgan: 2026-11-02 dalillarida 'tv' qutilari monitor edi). "
        "Maxsus modelda monitor 'monitor' yorlig‘i bilan o‘qitiladi.",
    ),
    ("all_in_one_pc", "Monoblok", ["all_in_one_pc", "monoblock", "all_in_one"], 0.60, 30, ""),
    ("laptop", "Noutbuk", ["laptop", "notebook"], 0.60, 40, ""),
    (
        "keyboard",
        "Klaviatura",
        ["keyboard"],
        0.70,
        50,
        "Kichik obyekt: loyiha qoidasi bo‘yicha 0.70 dan past ishonch qabul qilinmaydi. "
        "O‘lchovda (2026-11-02, 83 kadr) 0.55 da R 0.72, 0.70 da R 0.68, P ikkalasida 1.00 — "
        "ya'ni chegara faqat ~2 qutini yo‘qotadi, siyosat esa o‘zgartirilmadi.",
    ),
    ("mouse", "Sichqoncha", ["mouse"], 0.70, 60, "Kichik obyekt: 0.70 dan past ishonch qabul qilinmaydi."),
    ("computer_case", "Tizim bloki", ["computer_case", "system_unit", "case", "tower"], 0.60, 70, ""),
    ("printer", "Printer", ["printer"], 0.60, 80, ""),
    ("scanner", "Skaner", ["scanner"], 0.60, 90, ""),
    ("ups", "UPS", ["ups", "battery"], 0.60, 100, ""),
    ("speaker", "Dinamik", ["speaker", "soundspeaker"], 0.60, 110, ""),
    ("network_device", "Tarmoq uskunasi", ["network_device", "router", "switch"], 0.60, 120, ""),
    ("other_equipment", "Boshqa jihoz", ["other_equipment", "equipment"], 0.70, 130, ""),
    (
        "tv",
        "Televizor",
        ["television"],
        0.60,
        140,
        "COCO modelining 'tv' sinfi ofisda monitorni bildiradi, shuning uchun u Monitor toifasida. "
        "Haqiqiy televizor bo‘lsa 'television' yorlig‘ini shu toifaga qo‘shing.",
    ),
    ("projector", "Proyektor", ["projector", "video_projector", "beamer"], 0.60, 150, ""),
    (
        "ip_phone",
        "IP telefon",
        ["ip_phone", "ip-phone", "ipphone", "voip_phone", "desk_phone"],
        0.75,
        160,
        "Ofis IP telefoni. Maxsus modelda shu sinf nomlaridan foydalaning.",
    ),
    (
        "landline_phone",
        "Statsionar telefon",
        ["landline_phone", "home_phone", "landline", "telephone"],
        0.75,
        170,
        "Uy/ofis statsionar (domashniy) telefoni.",
    ),
    (
        "smartphone",
        "Smartfon",
        ["smartphone", "cell phone", "cellphone", "mobile_phone"],
        0.75,
        180,
        "COCO modelida 'cell phone' sinfiga to‘g‘ri keladi.",
    ),
    ("tablet", "Planshet", ["tablet", "ipad", "tablet_pc"], 0.75, 190, ""),
    (
        "camera",
        "Kamera (IP/CCTV)",
        ["camera", "ip_camera", "ip-camera", "cctv_camera", "security_camera", "webcam"],
        0.70,
        200,
        "Kuzatuv kamerasi. Maxsus modelda 'camera' yoki 'ip_camera' sinfi bilan o‘qitiladi.",
    ),
    (
        "mini_camera",
        "Mini IP kamera",
        ["mini_camera", "mini_ip_camera", "dome_camera", "bullet_camera"],
        0.75,
        210,
        "Kichik o‘lchamli kameralar; 0.75 dan past ishonch qabul qilinmaydi.",
    ),
    (
        "lan_tester",
        "LAN tester",
        ["lan_tester", "lan-tester", "cable_tester", "network_tester"],
        0.70,
        220,
        "Kabelni tekshiruvchi asbob (LAN tester).",
    ),
    (
        "lan_cable",
        "LAN kabel",
        ["lan_cable", "lan-cable", "network_cable", "ethernet_cable", "patch_cable", "utp_cable"],
        0.70,
        230,
        "Yupqa va uzun obyekt: shakl tekshiruvi (juda yupqa/uzun) bilan birga 0.70 chegara qo‘llanadi.",
    ),
    ("external_hdd", "Tashqi disk", ["external_hdd", "hard_disk", "external_drive", "portable_hdd"], 0.70, 240, ""),
    ("flash_drive", "USB flesh", ["flash_drive", "usb_drive", "usb_flash", "flashdisk"], 0.70, 250, ""),
    ("server", "Server", ["server", "rack_server", "server_rack"], 0.60, 260, ""),
]

# Hard floor per category, applied to rows that already exist in the database.
# A general (COCO) model reports desk clutter at 0.3-0.5 confidence and those
# boxes are exactly the "mouse seen as a phone" noise: nothing below 0.60 is
# trusted at all, handheld devices need 0.70-0.75. ``person`` stays lower
# because tracking a distant employee matters more than box precision.
MIN_CONFIDENCE_BY_CODE: dict[str, float] = {
    "smartphone": 0.75,
    "tablet": 0.75,
    "ip_phone": 0.75,
    "landline_phone": 0.75,
    "mini_camera": 0.75,
    "camera": 0.70,
    "mouse": 0.70,
    "keyboard": 0.70,
    "flash_drive": 0.70,
    "external_hdd": 0.70,
    "lan_tester": 0.70,
    "lan_cable": 0.70,
    "other_equipment": 0.70,
}
DEFAULT_MIN_CONFIDENCE = 0.60


def enforce_confidence_floors(session: Session) -> int:
    """Raise stored thresholds to the accuracy baseline (never lower them)."""
    changed = 0
    for category in session.scalars(select(EquipmentCategory)).all():
        if category.code == "person":
            continue
        target = MIN_CONFIDENCE_BY_CODE.get(category.code, DEFAULT_MIN_CONFIDENCE)
        current = category.min_confidence
        if current is not None and float(current) >= target:
            continue
        category.min_confidence = target
        changed += 1
        logger.info(
            "Aniqlik chegarasi ko‘tarildi: %s %.2f -> %.2f",
            category.code,
            float(current or 0.0),
            target,
        )
    return changed


def resolve_label_ownership(session: Session) -> int:
    """Make every YOLO label belong to exactly one category.

    Categories are seeded and edited independently, so the same class name can
    end up on two rows (``tv`` on both ``monitor`` and ``tv``). ``build_rules``
    would then map the label to whichever row came last, which is fragile: the
    more specific category (the highest ``sort_order``) claims the label and the
    duplicates are removed from the other rows.
    """
    categories = list(
        session.scalars(
            select(EquipmentCategory).order_by(EquipmentCategory.sort_order, EquipmentCategory.id)
        ).all()
    )
    owner: dict[str, str] = {}
    for category in reversed(categories):
        for label in category.yolo_labels or []:
            owner.setdefault(str(label).strip().lower(), category.code)

    changed = 0
    for category in categories:
        current = [str(item).strip() for item in (category.yolo_labels or [])]
        kept = [label for label in current if owner.get(label.lower()) == category.code]
        if len(kept) == len(current):
            continue
        dropped = [label for label in current if label not in kept]
        category.yolo_labels = kept
        changed += 1
        logger.info(
            "YOLO sinflari boshqa toifaga o‘tkazildi: %s (chiqarildi: %s)",
            category.code,
            ", ".join(dropped),
        )
    return changed



def seed_categories(session: Session) -> int:
    """Insert missing default categories. Existing rows are never overwritten."""
    existing = {code for (code,) in session.execute(select(EquipmentCategory.code)).all()}
    created = 0
    for code, label, labels, conf, order, notes in DEFAULT_CATEGORIES:
        if code in existing:
            continue
        session.add(
            EquipmentCategory(
                code=code,
                label_uz=label,
                yolo_labels=labels,
                is_person=(code == "person"),
                min_confidence=conf,
                sort_order=order,
                notes=notes or None,
            )
        )
        created += 1
    if created:
        session.flush()
        logger.info("Equipment categories seeded: %d", created)
    resolve_label_ownership(session)
    enforce_confidence_floors(session)
    return created


def ensure_bootstrap_admin(session: Session, settings: Settings) -> None:
    user_count = session.scalar(select(func.count(User.id))) or 0
    if user_count:
        return
    admin = User(
        username=settings.bootstrap_admin_username,
        full_name=settings.bootstrap_admin_fullname,
        password_hash=hash_password(settings.bootstrap_admin_password),
        role=Role.ADMIN.value,
        is_active=True,
    )
    session.add(admin)
    session.flush()
    logger.warning(
        "Bootstrap administrator yaratildi: %s (parolni Sozlamalar bo‘limida o‘zgartiring)",
        admin.username,
    )


def ensure_sample_camera(session: Session) -> None:
    """Seed one *disabled* simulation camera so the live page is usable offline."""
    if session.scalar(select(func.count(Camera.id))):
        return
    session.add(
        Camera(
            code="SIM-01",
            name="Sinov kamerasi (simulyatsiya)",
            location="Ofis",
            source_type=SourceType.SIMULATION.value,
            source="simulation://demo",
            is_enabled=False,
            status=CameraStatus.DISABLED.value,
            status_message="Namuna kamera. Haqiqiy RTSP manzilini Sozlamalarda kiriting.",
            settings={"confidence": None},
        )
    )
    session.flush()


def bootstrap_database(settings: Settings) -> None:
    from app.database import session_scope

    with session_scope() as session:
        seed_categories(session)
        ensure_bootstrap_admin(session, settings)
        ensure_sample_camera(session)
