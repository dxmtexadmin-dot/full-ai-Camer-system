"""Detection rules, duplicate suppression and per-track category voting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import EquipmentCategory
from app.services import equipment_service
from app.services.bootstrap import (
    DEFAULT_CATEGORIES,
    MIN_CONFIDENCE_BY_CODE,
    enforce_confidence_floors,
    resolve_label_ownership,
)
from app.services.tracking_service import TrackMemory
from app.services.types import BBox, Detection
from app.services.yolo_service import (
    CategoryRule,
    YoloService,
    build_rules,
    dedupe_overlaps,
    plausible_box,
    resolve_confusions,
)

T0 = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)

NEW_CODES = {
    "tv",
    "projector",
    "ip_phone",
    "landline_phone",
    "smartphone",
    "tablet",
    "camera",
    "mini_camera",
    "lan_tester",
    "lan_cable",
    "external_hdd",
    "flash_drive",
    "server",
}


def box(x1: float, y1: float, x2: float, y2: float, *, category: str, confidence: float = 0.9) -> Detection:
    return Detection(
        bbox=BBox(x1, y1, x2, y2),
        confidence=confidence,
        label=category,
        category=category,
        is_person=False,
        track_id=1,
    )


# -- categories ---------------------------------------------------------------
def test_new_telecom_and_network_categories_are_seeded(session):
    codes = {code for (code,) in session.execute(select(EquipmentCategory.code)).all()}

    assert NEW_CODES <= codes
    assert len(codes) == len(DEFAULT_CATEGORIES)


def test_coco_tv_label_is_owned_by_the_monitor_category(session):
    """COCO `tv` qutisi ofisda monitorni bildiradi (dalillar bilan tekshirilgan).

    Aks holda ekran "Televizor" nomi bilan yozilardi; haqiqiy televizor uchun
    `television` yorlig‘i qoldirilgan.
    """
    monitor = equipment_service.category_by_code(session, "monitor")
    tv = equipment_service.category_by_code(session, "tv")

    assert "tv" in monitor.yolo_labels
    assert "tvmonitor" in monitor.yolo_labels
    assert "tv" not in tv.yolo_labels
    assert tv.yolo_labels == ["television"]

    rules = build_rules(equipment_service.categories(session))
    assert rules["tv"].code == "monitor"
    assert rules["tvmonitor"].code == "monitor"
    assert rules["television"].code == "tv"
    assert rules["cell phone"].code == "smartphone"
    assert rules["ip-phone"].code == "ip_phone"


def test_no_category_sits_on_a_dead_confidence_floor(session):
    """0.90+ chegaralar model mavjud bo‘lganda ham hech narsani o‘tkazmaydi."""
    rules = build_rules(equipment_service.categories(session))

    for code in ("mouse", "desktop_pc", "keyboard"):
        assert rules[code].min_confidence is not None
        assert rules[code].min_confidence <= 0.75


def test_equipment_floors_stay_above_the_project_floor(session):
    """O‘lchov (83 kadr): keyboard 0.55 da P 1.00/R 0.72, 0.70 da P 1.00/R 0.68.

    Farq ~2 quti — loyihaning 0.60 minimal chegarasi saqlanadi.
    """
    rules = build_rules(equipment_service.categories(session))
    assert 0.60 <= rules["keyboard"].min_confidence <= 0.75


def test_label_ownership_is_idempotent(session):
    assert resolve_label_ownership(session) == 0


def test_new_categories_get_their_own_asset_prefixes(session):
    assert equipment_service.next_asset_code(session, "ip_phone").startswith("IPP-")
    assert equipment_service.next_asset_code(session, "lan_cable").startswith("CBL-")
    assert equipment_service.next_asset_code(session, "tv").startswith("TV-")
    assert equipment_service.next_asset_code(session, "mini_camera").startswith("MCAM-")


# -- duplicate suppression ----------------------------------------------------
def test_dedupe_keeps_the_more_confident_category():
    detections = [
        box(100, 100, 300, 260, category="tv", confidence=0.62),
        box(102, 104, 298, 258, category="monitor", confidence=0.51),
    ]

    kept = dedupe_overlaps(detections, 0.6)

    assert [item.category for item in kept] == ["tv"]


def test_dedupe_ignores_separate_objects_and_same_category():
    detections = [
        box(100, 100, 200, 200, category="monitor"),
        box(600, 400, 700, 500, category="tv"),
        box(100, 100, 200, 200, category="monitor", confidence=0.7),
    ]

    kept = dedupe_overlaps(detections, 0.6)

    assert len(kept) == 3


# -- per-track voting ---------------------------------------------------------
def test_track_category_uses_the_majority_label():
    memory = TrackMemory()

    memory.update([box(100, 100, 300, 260, category="tv")], T0)
    memory.update([box(102, 100, 302, 260, category="monitor")], T0 + timedelta(seconds=1))
    memory.update([box(104, 100, 304, 260, category="monitor")], T0 + timedelta(seconds=2))

    assert memory.get(1).category == "monitor"


def test_employee_match_keeps_the_strongest_score():
    memory = TrackMemory()
    memory.update([box(100, 100, 300, 500, category="person")], T0)

    memory.assign_employee(1, 7, "Otabek", 0.82)
    memory.assign_employee(1, 7, "Otabek", 0.61)
    memory.assign_employee(1, 7, "Otabek", 0.91)

    assert memory.get(1).face_score == 0.91
    assert memory.get(1).employee_name == "Otabek"


# -- confidence floors --------------------------------------------------------
def test_inference_floor_follows_the_loosest_category(settings):
    rules = {
        "tv": CategoryRule("tv", "Televizor", False, 0.40),
        "lan_cable": CategoryRule("lan_cable", "LAN kabel", False, 0.25),
    }
    service = YoloService(settings, rules=rules)

    assert service._inference_confidence(None) == 0.25
    assert service._effective_min_confidence(rules["tv"], None) == 0.40
    assert service._inference_confidence(None) < settings.yolo_confidence


def test_inference_floor_never_drops_below_a_quarter(settings):
    rules = {"lan_cable": CategoryRule("lan_cable", "LAN kabel", False, 0.1)}
    service = YoloService(settings, rules=rules)

    assert service._inference_confidence(None) == 0.25


def test_inference_floor_does_not_raise_the_global_threshold(settings):
    rules = {"person": CategoryRule("person", "Shaxs", True, 0.45)}
    service = YoloService(settings, rules=rules)

    assert service._inference_confidence(None) == settings.yolo_confidence


def test_category_without_a_threshold_uses_the_global_value(settings):
    service = YoloService(settings, rules={})
    unset = CategoryRule("unknown", "Noma’lum", False, None)

    assert service._effective_min_confidence(unset, None) == settings.yolo_confidence


# -- accuracy: stored floors ---------------------------------------------------
def test_handheld_floors_are_at_least_seventy_percent(session):
    rules = build_rules(equipment_service.categories(session))

    assert rules["cell phone"].min_confidence == 0.75
    assert rules["tablet"].min_confidence == 0.75
    assert rules["mouse"].min_confidence == 0.70
    assert rules["keyboard"].min_confidence == 0.70
    assert rules["laptop"].min_confidence == 0.60
    # People are tracked on purpose at a looser floor (distance, faces).
    assert rules["person"].min_confidence == 0.45


def test_nothing_is_trusted_below_sixty_percent(session):
    rules = build_rules(equipment_service.categories(session))

    for label, rule in rules.items():
        if rule.is_person:
            continue
        assert rule.min_confidence >= 0.60, label


def test_existing_low_floors_are_raised_on_startup(session):
    category = equipment_service.category_by_code(session, "smartphone")
    category.min_confidence = 0.30
    session.flush()

    changed = enforce_confidence_floors(session)

    assert changed >= 1
    assert category.min_confidence == MIN_CONFIDENCE_BY_CODE["smartphone"]


def test_high_floors_set_by_the_admin_are_not_lowered(session):
    category = equipment_service.category_by_code(session, "mouse")
    category.min_confidence = 0.93
    session.flush()

    enforce_confidence_floors(session)

    assert category.min_confidence == 0.93


# -- accuracy: shape and class-confusion filters -------------------------------
def test_a_wide_flat_box_is_not_a_phone():
    frame = (480, 640, 3)

    # A mouse lying on the desk: 90 x 45 px.
    assert plausible_box("smartphone", BBox(100, 100, 190, 145), frame) is False
    # A phone held in a hand: 40 x 90 px.
    assert plausible_box("smartphone", BBox(100, 100, 140, 190), frame) is True


def test_a_tiny_blob_is_not_a_phone():
    frame = (480, 640, 3)

    assert plausible_box("smartphone", BBox(100, 100, 108, 116), frame) is False


def test_a_phone_box_larger_than_a_hand_is_rejected():
    frame = (480, 640, 3)

    # 400 x 600 px would cover most of the frame - not a handset.
    assert plausible_box("smartphone", BBox(100, 50, 500, 650), frame) is False


def test_a_mouse_held_close_to_the_camera_is_still_a_mouse():
    frame = (480, 640, 3)

    # Qo'ldagi maus kameraga yaqin tutilganda kadrning ~18 % ini egallaydi.
    # Eski 6 % chegarasi to'g'ri qutini ham tashlab yuborardi.
    assert plausible_box("mouse", BBox(160, 150, 480, 320), frame) is True


def test_a_mouse_held_upright_in_a_hand_is_still_a_mouse():
    frame = (480, 640, 3)

    # Dalil kadridan olingan haqiqiy holat: maus tik tutilganda w/h = 0.65
    # (119x183 px). Eski 0.80 pastki chegara bu qutini rad etardi.
    assert plausible_box("mouse", BBox(337, 235, 456, 418), frame) is True
    # Maus hech qachon qalamdek ingichka bo'lmaydi.
    assert plausible_box("mouse", BBox(300, 200, 330, 400), frame) is False


def test_a_big_box_is_not_a_phone_even_when_the_shape_fits():
    frame = (480, 640, 3)

    # Nisbat telefonnikiga o'xshaydi, lekin maydoni 24 % — qo'lda bunchalik katta telefon yo'q.
    assert plausible_box("smartphone", BBox(160, 120, 460, 370), frame) is False


def test_categories_without_a_shape_rule_pass_through():
    assert plausible_box("laptop", BBox(0, 0, 3, 3), (480, 640, 3)) is True


def test_phone_label_on_top_of_a_mouse_is_dropped():
    detections = [
        box(100, 100, 190, 145, category="mouse", confidence=0.72),
        box(104, 104, 180, 150, category="smartphone", confidence=0.81),
    ]

    kept = resolve_confusions(detections)

    assert [item.category for item in kept] == ["mouse"]


def test_a_phone_far_from_the_desk_survives():
    detections = [
        box(100, 100, 190, 145, category="mouse"),
        box(400, 60, 440, 150, category="smartphone"),
    ]

    kept = resolve_confusions(detections)

    assert {item.category for item in kept} == {"mouse", "smartphone"}
