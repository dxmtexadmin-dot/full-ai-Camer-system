"""Fallback tracker, track memory and the virtual line crossing detector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.enums import Direction
from app.services.tracking_service import LineCrossingDetector, SimpleTracker, TrackMemory, point_in_polygon
from app.services.types import BBox, Detection

T0 = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)


def detection(bbox: tuple, is_person: bool = True, track_id: int = 1) -> Detection:
    return Detection(
        bbox=BBox(*bbox),
        confidence=0.9,
        label="person" if is_person else "laptop",
        category="person" if is_person else "laptop",
        is_person=is_person,
    ).with_track(track_id)


def test_tracker_keeps_id_for_moving_object():
    tracker = SimpleTracker()
    first = tracker.update([detection((100, 100, 200, 300))])
    second = tracker.update([detection((110, 102, 210, 302))])
    assert first[0].track_id == second[0].track_id


def test_tracker_assigns_new_id_after_disappearing():
    tracker = SimpleTracker(max_age=1)
    first = tracker.update([detection((100, 100, 200, 300))])
    tracker.update([])
    tracker.update([])
    later = tracker.update([detection((600, 100, 700, 300))])
    assert later[0].track_id != first[0].track_id


def test_tracker_never_mixes_people_and_equipment():
    tracker = SimpleTracker()
    tracker.update([detection((100, 100, 200, 300), is_person=True)])
    equipment = tracker.update([detection((110, 110, 190, 290), is_person=False)])
    assert equipment[0].track_id == 2


def test_track_memory_accumulates_frames_and_velocity():
    memory = TrackMemory()
    memory.update([detection((100, 100, 200, 300))], T0)
    memory.update([detection((120, 100, 220, 300))], T0 + timedelta(seconds=1))
    states = memory.update([detection((140, 100, 240, 300))], T0 + timedelta(seconds=2))

    assert states[0].frames_seen == 3
    assert states[0].velocity[0] > 0
    assert memory.person_features()[0].track_id == states[0].track_id
    assert memory.equipment_features() == []


def test_track_memory_prunes_stale_tracks():
    memory = TrackMemory()
    memory.update([detection((100, 100, 200, 300))], T0)
    memory.prune(T0 + timedelta(seconds=60), max_age_seconds=30)
    assert memory.states == {}


def test_face_retry_interval_is_respected():
    memory = TrackMemory()
    memory.update([detection((100, 100, 200, 300))], T0)
    assert memory.should_try_face(1, interval=5) is True
    memory.mark_face_attempt(1)
    assert memory.should_try_face(1, interval=5) is False


# Convention: crossing from the negative side to the positive side is "IN".
ABOVE = (640, 300)  # positive side of the horizontal line y=360
BELOW = (640, 420)  # negative side


def test_line_crossing_reports_in_and_out():
    detector = LineCrossingDetector(((0, 360), (1280, 360)), cooldown_seconds=0, hysteresis_px=1)

    assert detector.update(1, BELOW, T0) is None  # first sample only stores the side
    assert detector.update(1, ABOVE, T0 + timedelta(seconds=1)) is Direction.IN
    assert detector.update(1, BELOW, T0 + timedelta(seconds=2)) is Direction.OUT


def test_line_crossing_respects_cooldown():
    detector = LineCrossingDetector(((0, 360), (1280, 360)), cooldown_seconds=10, hysteresis_px=1)
    detector.update(1, BELOW, T0)
    assert detector.update(1, ABOVE, T0 + timedelta(seconds=1)) is Direction.IN
    assert detector.update(1, BELOW, T0 + timedelta(seconds=2)) is None


def test_line_crossing_ignores_noise_inside_hysteresis_band():
    detector = LineCrossingDetector(((0, 360), (1280, 360)), cooldown_seconds=0, hysteresis_px=10)
    detector.update(1, BELOW, T0)
    assert detector.update(1, (640, 365), T0 + timedelta(seconds=1)) is None
    assert detector.update(1, ABOVE, T0 + timedelta(seconds=2)) is Direction.IN


def test_line_crossing_flip_swaps_direction():
    detector = LineCrossingDetector(((0, 360), (1280, 360)), cooldown_seconds=0, hysteresis_px=1, flip=True)
    detector.update(1, BELOW, T0)
    assert detector.update(1, ABOVE, T0 + timedelta(seconds=1)) is Direction.OUT


def test_line_crossing_requires_minimum_track_age():
    detector = LineCrossingDetector(((0, 360), (1280, 360)), cooldown_seconds=0, hysteresis_px=1, min_track_frames=5)
    detector.update(1, BELOW, T0, frames_seen=1)
    assert detector.update(1, ABOVE, T0 + timedelta(seconds=1), frames_seen=2) is None
    assert detector.update(1, BELOW, T0 + timedelta(seconds=2), frames_seen=6) is Direction.OUT


@pytest.mark.parametrize(
    ("point", "expected"),
    [((10, 10), True), ((50, 50), True), ((100, 100), False)],
)
def test_point_in_polygon(point, expected):
    polygon = [(0, 0), (60, 0), (60, 60), (0, 60)]
    assert point_in_polygon(point, polygon) is expected


def test_point_in_polygon_needs_three_points():
    assert point_in_polygon((5, 5), [(0, 0), (10, 10)]) is False
