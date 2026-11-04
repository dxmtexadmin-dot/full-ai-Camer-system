"""Multi-object tracking helpers.

Two pieces live here:

* ``SimpleTracker`` - a dependency-free IoU tracker used when Ultralytics /
  ByteTrack is not installed (CPU smoke tests, unit tests, simulation).
* ``TrackMemory`` - per-camera state (track age, velocity, identity bindings)
  feeding the association engine and the live overlay.
* ``LineCrossingDetector`` - geometry + debounce for entrance/exit detection.

Track ids are deliberately temporary. They never become permanent employee or
asset identities; they only support proximity/temporal reasoning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from app.models.enums import Direction
from app.services.types import BBox, Detection, TrackFeature


# ---------------------------------------------------------------------------
# Fallback tracker
# ---------------------------------------------------------------------------
class SimpleTracker:
    """Greedy IoU tracker, good enough when ByteTrack is unavailable."""

    def __init__(self, *, iou_threshold: float = 0.3, max_age: int = 15) -> None:
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self._next_id = 1
        self._tracks: dict[int, dict] = {}

    def reset(self) -> None:
        self._next_id = 1
        self._tracks.clear()

    def update(self, detections: Iterable[Detection]) -> list[Detection]:
        detections = list(detections)
        for track in self._tracks.values():
            track["age"] += 1

        assigned: list[Detection] = []
        used_tracks: set[int] = set()

        # Greedy: best IoU first, per detection.
        candidates: list[tuple[float, int, int]] = []
        for det_index, det in enumerate(detections):
            for track_id, track in self._tracks.items():
                if track["is_person"] != det.is_person:
                    continue
                iou = det.bbox.iou(track["bbox"])
                if iou >= self.iou_threshold:
                    candidates.append((iou, det_index, track_id))
        candidates.sort(reverse=True)

        mapping: dict[int, int] = {}
        for _iou, det_index, track_id in candidates:
            if det_index in mapping or track_id in used_tracks:
                continue
            mapping[det_index] = track_id
            used_tracks.add(track_id)

        for det_index, det in enumerate(detections):
            track_id = mapping.get(det_index)
            if track_id is None:
                track_id = self._next_id
                self._next_id += 1
                self._tracks[track_id] = {
                    "bbox": det.bbox,
                    "is_person": det.is_person,
                    "age": 0,
                }
            else:
                self._tracks[track_id]["bbox"] = det.bbox
                self._tracks[track_id]["age"] = 0
            assigned.append(det.with_track(track_id))

        for track_id in [tid for tid, track in self._tracks.items() if track["age"] > self.max_age]:
            self._tracks.pop(track_id, None)
        return assigned


# ---------------------------------------------------------------------------
# Per-camera memory
# ---------------------------------------------------------------------------
@dataclass
class TrackState:
    track_id: int
    category: str
    is_person: bool
    bbox: BBox
    confidence: float
    first_seen: datetime
    last_seen: datetime
    frames_seen: int = 1
    velocity: tuple[float, float] = (0.0, 0.0)
    category_votes: dict[str, int] = field(default_factory=dict)
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    face_score: Optional[float] = None
    last_face_frame: int = -10_000
    crossings: list[tuple[datetime, str]] = field(default_factory=list)

    def feature(self) -> TrackFeature:
        return TrackFeature(
            track_id=self.track_id,
            bbox=self.bbox,
            is_person=self.is_person,
            category=self.category,
            confidence=self.confidence,
            frames_seen=self.frames_seen,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
            velocity=self.velocity,
            employee_id=self.employee_id,
            employee_name=self.employee_name,
            face_score=self.face_score,
        )


class TrackMemory:
    """Keeps short-lived state for every track seen by one camera."""

    def __init__(self, *, frame_counter_start: int = 0) -> None:
        self.states: dict[int, TrackState] = {}
        self.frame_index = frame_counter_start

    def reset(self) -> None:
        self.states.clear()

    def update(self, detections: Iterable[Detection], now: datetime) -> list[TrackState]:
        """Update memory with this frame's tracked detections."""
        self.frame_index += 1
        seen: set[int] = set()
        for det in detections:
            if det.track_id is None:
                continue
            seen.add(det.track_id)
            state = self.states.get(det.track_id)
            if state is None:
                self.states[det.track_id] = TrackState(
                    track_id=det.track_id,
                    category=det.category,
                    is_person=det.is_person,
                    bbox=det.bbox,
                    confidence=det.confidence,
                    first_seen=now,
                    last_seen=now,
                    category_votes={det.category: 1},
                )
                continue
            dx, dy = det.bbox.cx - state.bbox.cx, det.bbox.cy - state.bbox.cy
            state.velocity = (
                round(0.6 * state.velocity[0] + 0.4 * dx, 2),
                round(0.6 * state.velocity[1] + 0.4 * dy, 2),
            )
            state.bbox = det.bbox
            state.confidence = det.confidence
            # A model can flip between two labels on the same physical object
            # (tv <-> monitor). Majority voting over the track keeps the ledger
            # category stable instead of following every noisy frame.
            state.category_votes[det.category] = state.category_votes.get(det.category, 0) + 1
            state.category = max(state.category_votes.items(), key=lambda item: item[1])[0]
            state.last_seen = now
            state.frames_seen += 1
        return [self.states[tid] for tid in seen]

    def prune(self, now: datetime, max_age_seconds: float = 30.0) -> None:
        for track_id in list(self.states):
            state = self.states[track_id]
            if (now - state.last_seen).total_seconds() > max_age_seconds:
                self.states.pop(track_id, None)

    def person_features(self) -> list[TrackFeature]:
        return [s.feature() for s in self.states.values() if s.is_person]

    def equipment_features(self) -> list[TrackFeature]:
        return [s.feature() for s in self.states.values() if not s.is_person]

    def get(self, track_id: int) -> Optional[TrackState]:
        return self.states.get(track_id)

    def assign_employee(self, track_id: int, employee_id: int, name: str, score: float) -> None:
        state = self.states.get(track_id)
        if state is None:
            return
        # Keep the strongest identification: a later blurry frame with a lower
        # score must not downgrade a confident match.
        if state.face_score is not None and float(score) <= float(state.face_score):
            return
        state.employee_id = employee_id
        state.employee_name = name
        state.face_score = score

    def mark_face_attempt(self, track_id: int) -> None:
        state = self.states.get(track_id)
        if state is not None:
            state.last_face_frame = self.frame_index

    def should_try_face(self, track_id: int, interval: int) -> bool:
        state = self.states.get(track_id)
        if state is None:
            return False
        return (self.frame_index - state.last_face_frame) >= interval

    def record_crossing(self, track_id: int, moment: datetime, direction: Direction) -> None:
        state = self.states.get(track_id)
        if state is not None:
            state.crossings.append((moment, direction.value))


# ---------------------------------------------------------------------------
# Line crossing
# ---------------------------------------------------------------------------
class LineCrossingDetector:
    """Detect movement across a virtual line with hysteresis and cooldown.

    Convention: ``side = sign((p2 - p1) x (point - p1))``.
    A crossing from negative to positive is reported as ``Direction.IN``;
    positive to negative as ``Direction.OUT`` (both reversed when
    ``flip=True`` is configured for the camera).
    """

    def __init__(
        self,
        line: tuple[tuple[float, float], tuple[float, float]],
        *,
        cooldown_seconds: float = 20.0,
        hysteresis_px: float = 6.0,
        flip: bool = False,
        min_track_frames: int = 3,
    ) -> None:
        self.p1, self.p2 = line
        self.cooldown_seconds = cooldown_seconds
        self.hysteresis_px = hysteresis_px
        self.flip = flip
        self.min_track_frames = min_track_frames
        self._last_side: dict[int, int] = {}
        self._last_crossing: dict[int, datetime] = {}
        self._last_point: dict[int, tuple[float, float]] = {}

    def signed_distance(self, point: tuple[float, float]) -> float:
        (x1, y1), (x2, y2) = self.p1, self.p2
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length == 0:
            return 0.0
        return ((point[0] - x1) * dy - (point[1] - y1) * dx) / length

    def update(
        self,
        track_id: int,
        point: tuple[float, float],
        now: datetime,
        *,
        frames_seen: int = 0,
    ) -> Optional[Direction]:
        distance = self.signed_distance(point)
        side = 0 if abs(distance) < self.hysteresis_px else (1 if distance > 0 else -1)
        previous = self._last_side.get(track_id)
        self._last_point[track_id] = point

        if side != 0:
            self._last_side[track_id] = side

        if previous is None or side == 0 or side == previous:
            return None
        if frames_seen and frames_seen < self.min_track_frames:
            return None

        last = self._last_crossing.get(track_id)
        if last is not None and (now - last).total_seconds() < self.cooldown_seconds:
            return None

        self._last_crossing[track_id] = now
        direction = Direction.IN if previous < side else Direction.OUT
        if self.flip:
            direction = Direction.OUT if direction is Direction.IN else Direction.IN
        return direction

    def reset(self) -> None:
        self._last_side.clear()
        self._last_crossing.clear()
        self._last_point.clear()


def line_from_camera(camera) -> Optional[tuple[tuple[float, float], tuple[float, float]]]:
    if not getattr(camera, "has_line", False):
        return None
    return camera.line


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    """Ray casting; used for optional entrance/exit zones."""
    x, y = point
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = (yi > y) != (yj > y)
        if intersects:
            x_cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside
