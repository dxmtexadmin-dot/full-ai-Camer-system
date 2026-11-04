"""Possession lifecycle: pick-up binds an object to a person until put down.

The pipeline must answer two questions from the dashboard table:
*who took what* (equipment tied to an employee) and *when it left* (entry/exit
event with its timestamps).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import select

from app.models import InventoryTransaction
from app.models.enums import EQUIPMENT_EVENT_TYPES, EventType
from app.services import event_service, pipeline_service
from app.services.camera_service import CameraConfig
from app.services.pipeline_service import FrameAnalyzer
from app.services.types import BBox, Detection

FRAME = np.zeros((480, 640, 3), dtype=np.uint8)


class Clock:
    """Deterministic clock; FrameAnalyzer reads the module-level utc_now."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 29, 8, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float = 0.3) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


class Detector:
    """Scene description instead of a YOLO model: the test places the boxes."""

    def __init__(self) -> None:
        self.boxes: list[tuple[str, str, bool, tuple[float, float, float, float], float]] = []

    def person(self, box: tuple[float, float, float, float], confidence: float = 0.9) -> "Detector":
        self.boxes.append(("person", "person", True, box, confidence))
        return self

    def item(
        self,
        category: str,
        box: tuple[float, float, float, float],
        confidence: float = 0.8,
    ) -> "Detector":
        self.boxes.append((category, category, False, box, confidence))
        return self

    def clear(self) -> "Detector":
        self.boxes.clear()
        return self

    def detect(self, frame: np.ndarray, **_: object) -> list[Detection]:
        return [
            Detection(bbox=BBox(*box), confidence=conf, label=label, category=category, is_person=is_person)
            for label, category, is_person, box, conf in self.boxes
        ]


def camera_config(line=None) -> CameraConfig:
    return CameraConfig(
        id=1,
        code="CAM",
        name="Test kamera",
        location="Kirish",
        source_type="usb",
        source="0",
        is_enabled=True,
        line=line,
    )


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(pipeline_service, "utc_now", clock)
    return clock


def analyzer_for(settings, detector, line=None) -> FrameAnalyzer:
    return FrameAnalyzer(camera_config(line), settings, detector=detector)


def run(analyzer, clock, frames: int, step: float = 0.3):
    """Process frames, returning every possession/movement event produced."""
    events = []
    for _ in range(frames):
        result = analyzer.process(FRAME)
        events.extend(result.possessions)
        events.extend(result.movements)
        clock.advance(step)
    return events


def test_pickup_binds_the_object_after_a_few_frames(settings, clock):
    detector = Detector().person((180, 100, 300, 400)).item("laptop", (200, 220, 300, 300))
    analyzer = analyzer_for(settings, detector)

    events = run(analyzer, clock, 3)

    assert [item.action for item in events] == ["pickup"]
    pickup = events[0]
    assert pickup.category == "laptop"
    assert pickup.person_track_id is not None
    assert pickup.held_seconds > 0
    assert len(analyzer.active_possessions()) == 1


def test_a_single_frame_does_not_bind_anything(settings, clock):
    detector = Detector().person((180, 100, 300, 400)).item("laptop", (200, 220, 300, 300))
    analyzer = analyzer_for(settings, detector)

    events = run(analyzer, clock, 2)

    assert events == []
    assert analyzer.active_possessions() == []


def test_binding_survives_a_short_association_gap(settings, clock):
    detector = Detector().person((180, 100, 300, 400)).item("laptop", (200, 220, 300, 300))
    analyzer = analyzer_for(settings, detector)
    run(analyzer, clock, 4)
    assert len(analyzer.active_possessions()) == 1

    # The laptop box flickers out for one frame and comes back: no put-down.
    detector.clear().person((180, 100, 300, 400))
    run(analyzer, clock, 1)
    detector.item("laptop", (200, 220, 300, 300))
    run(analyzer, clock, 2)

    assert len(analyzer.active_possessions()) == 1


def test_put_down_is_reported_when_the_object_stays_behind(settings, clock):
    detector = Detector().person((180, 100, 300, 400)).item("laptop", (200, 220, 300, 300))
    analyzer = analyzer_for(settings, detector)
    run(analyzer, clock, 4)

    detector.clear().item("laptop", (200, 220, 300, 300))
    events = run(analyzer, clock, 25, step=0.3)  # ~7.5 s > grace period

    put_downs = [item for item in events if item.action == "putdown"]
    assert len(put_downs) == 1
    assert put_downs[0].category == "laptop"
    assert put_downs[0].held_seconds > 0
    assert analyzer.active_possessions() == []


def test_an_object_far_from_the_body_is_never_bound(settings, clock):
    # Person on the left, laptop on the far right of the frame: nearby, but not
    # in the hands, so no pick-up may be invented.
    detector = Detector().person((60, 120, 180, 420)).item("laptop", (420, 300, 520, 380))
    analyzer = analyzer_for(settings, detector)

    events = run(analyzer, clock, 8)

    assert [item for item in events if item.action == "pickup"] == []
    assert analyzer.active_possessions() == []


def test_walking_away_from_the_object_closes_the_binding(settings, clock):
    detector = Detector().person((180, 100, 300, 400)).item("laptop", (200, 220, 300, 300))
    analyzer = analyzer_for(settings, detector)
    run(analyzer, clock, 4)
    assert len(analyzer.active_possessions()) == 1

    # The person walks to the other side of the room; the laptop stays put.
    detector.clear().person((400, 100, 520, 400)).item("laptop", (200, 220, 300, 300))
    events = run(analyzer, clock, 25, step=0.3)

    assert [item.action for item in events] == ["putdown"]
    assert analyzer.active_possessions() == []


def test_no_put_down_when_the_object_leaves_the_frame(settings, clock):
    detector = Detector().person((180, 100, 300, 400)).item("laptop", (200, 220, 300, 300))
    analyzer = analyzer_for(settings, detector)
    run(analyzer, clock, 4)

    detector.clear().person((180, 100, 300, 400))
    events = run(analyzer, clock, 25, step=0.3)

    assert [item for item in events if item.action == "putdown"] == []
    assert analyzer.active_possessions() == []


def test_crossing_reports_the_equipment_with_its_holder_and_hold_time(settings, clock):
    line = ((0.0, 250.0), (640.0, 250.0))
    detector = Detector()
    analyzer = analyzer_for(settings, detector, line=line)

    # Three frames above the line, then the pair walks through it.
    for top in (-60, -60, -60, -40, -20, 0):
        detector.clear().person((180, top, 300, top + 300)).item(
            "laptop", (200, top + 120, 280, top + 200)
        )
        result = analyzer.process(FRAME)
        if result.movements:
            movement = result.movements[0]
            break
        clock.advance(0.3)
    else:  # pragma: no cover - defensive
        pytest.fail("Crossing never produced an equipment movement")

    assert movement.category == "laptop"
    assert movement.person_track_id is not None
    assert movement.held_since is not None
    assert movement.held_seconds > 0
    assert "qo‘lda" in " ".join(movement.reasons)
    # The person was never recognised, so a human still has to confirm it.
    assert movement.employee_id is None
    assert movement.requires_review is True


def test_exit_event_still_names_the_equipment_after_it_leaves_the_frame(settings, clock):
    line = ((0.0, 250.0), (640.0, 250.0))
    detector = Detector().person((180, -60, 300, 240)).item("laptop", (200, 60, 280, 140))
    analyzer = analyzer_for(settings, detector, line=line)
    run(analyzer, clock, 4)
    assert len(analyzer.active_possessions()) == 1

    # The laptop is hidden behind the body or packed into a bag: the track
    # vanishes before the person reaches the line.
    detector.clear().person((180, -60, 300, 240))
    run(analyzer, clock, 25, step=0.3)  # longer than the grace period
    assert analyzer.active_possessions() == []

    events = []
    for offset in (0, 20, 40):
        detector.clear().person((180, -60 + offset, 300, 240 + offset))
        events.extend(analyzer.process(FRAME).movements)
        clock.advance(0.3)

    assert [item.category for item in events] == ["laptop"]
    movement = events[0]
    assert movement.person_track_id is not None
    assert movement.held_since is not None
    assert movement.held_seconds > 0
    assert "xotira" in " ".join(movement.reasons)
    # The object was not visible at the crossing, so a human confirms it.
    assert movement.requires_review is True


def test_carry_memory_expires_so_no_event_is_invented(settings, clock):
    line = ((0.0, 250.0), (640.0, 250.0))
    detector = Detector().person((180, -60, 300, 240)).item("laptop", (200, 60, 280, 140))
    analyzer = analyzer_for(settings, detector, line=line)
    run(analyzer, clock, 4)

    detector.clear().person((180, -60, 300, 240))
    run(analyzer, clock, 200, step=0.3)  # 60 s > carry_memory_seconds
    assert analyzer._carry_memory == {}

    events = []
    for offset in (0, 20, 40):
        detector.clear().person((180, -60 + offset, 300, 240 + offset))
        events.extend(analyzer.process(FRAME).movements)
        clock.advance(0.3)

    assert events == []


def test_possession_events_do_not_touch_the_inventory_ledger(session):
    event, created = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_PICKED_UP,
        camera_code="CAM",
        equipment_category="laptop",
        person_track_id=1,
        object_track_id=2,
    )
    session.commit()

    assert created is True
    assert EventType.EQUIPMENT_PICKED_UP not in EQUIPMENT_EVENT_TYPES
    assert session.scalars(select(InventoryTransaction)).all() == []
    assert event.equipment_category == "laptop"


def test_possession_dedupe_key_keeps_object_and_person(clock):
    base = dict(
        event_type=EventType.EQUIPMENT_PICKED_UP,
        camera_code="CAM",
        occurred_at=clock.now,
        cooldown_seconds=1,
        person_track_id=1,
    )
    first = event_service.build_dedupe_key(object_track_id=5, **base)
    second = event_service.build_dedupe_key(object_track_id=6, **base)

    assert first != second
    assert first.startswith("pos:CAM:EQUIPMENT_PICKED_UP:t5:p1:")


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def login(client) -> None:
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={"username": "admin", "password": "Admin123!", "next": "/", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def test_reports_card_hides_camera_status_noise(client, session):
    camera_event, _ = event_service.create_event(
        session, event_type=EventType.CAMERA_DISCONNECTED, camera_code="CAM"
    )
    person_event, _ = event_service.create_event(
        session, event_type=EventType.PERSON_ENTERED, camera_code="CAM", person_track_id=1
    )
    session.commit()

    login(client)
    page = client.get("/reports")

    assert page.status_code == 200
    assert person_event.public_id in page.text
    assert camera_event.public_id not in page.text


def test_full_history_still_shows_every_event(client, session):
    camera_event, _ = event_service.create_event(
        session, event_type=EventType.CAMERA_DISCONNECTED, camera_code="CAM"
    )
    session.commit()

    login(client)
    page = client.get("/movements")

    assert page.status_code == 200
    assert camera_event.public_id in page.text


def test_dashboard_lists_what_is_held_right_now(settings, clock):
    from types import SimpleNamespace

    from app.api.pages import active_holdings

    held = pipeline_service.Possession(
        object_track_id=7,
        category="laptop",
        person_track_id=3,
        started_at=clock.now,
        last_confirmed_at=clock.now + timedelta(seconds=12),
        employee_name="Otabek Bakhodirbekov",
    )
    analyzer = SimpleNamespace(active_possessions=lambda: [held])
    monitor = SimpleNamespace(runtimes={"CAM": SimpleNamespace(pipeline=SimpleNamespace(analyzer=analyzer))})

    rows = active_holdings(monitor)

    assert rows == [
        {
            "camera": "CAM",
            "category": "laptop",
            "employee_name": "Otabek Bakhodirbekov",
            "since": clock.now,
            "held_seconds": 12.0,
        }
    ]


def test_dashboard_renders_the_holdings_card(client):
    login(client)
    page = client.get("/")

    assert page.status_code == 200
    assert "Hozir qo‘lda" in page.text


def test_active_possessions_never_raises_while_frames_are_processed(settings, clock):
    detector = Detector().person((180, 100, 300, 400)).item("laptop", (200, 220, 300, 300))
    analyzer = analyzer_for(settings, detector)
    run(analyzer, clock, 4)

    assert len(analyzer.active_possessions()) == 1
    assert analyzer.active_possessions()[0].category == "laptop"
