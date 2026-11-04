"""Small value objects shared by detection, tracking and association."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class BBox:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0

    @property
    def cx(self) -> float:
        return self.center[0]

    @property
    def cy(self) -> float:
        return self.center[1]

    @property
    def diagonal(self) -> float:
        return (self.width**2 + self.height**2) ** 0.5

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.x1, self.y1, self.x2, self.y2

    def as_int(self) -> tuple[int, int, int, int]:
        return int(round(self.x1)), int(round(self.y1)), int(round(self.x2)), int(round(self.y2))

    def iou(self, other: "BBox") -> float:
        ix1, iy1 = max(self.x1, other.x1), max(self.y1, other.y1)
        ix2, iy2 = min(self.x2, other.x2), min(self.y2, other.y2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        intersection = iw * ih
        union = self.area + other.area - intersection
        return intersection / union if union > 0 else 0.0

    def expanded(self, ratio: float) -> "BBox":
        dx, dy = self.width * ratio / 2.0, self.height * ratio / 2.0
        return BBox(self.x1 - dx, self.y1 - dy, self.x2 + dx, self.y2 + dy)

    def contains_point(self, point: tuple[float, float]) -> bool:
        return self.x1 <= point[0] <= self.x2 and self.y1 <= point[1] <= self.y2

    def center_distance(self, other: "BBox") -> float:
        (cx1, cy1), (cx2, cy2) = self.center, other.center
        return ((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2) ** 0.5

    def bottom_center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2.0, self.y2


@dataclass
class Detection:
    """One bounding box produced by the detector for one frame."""

    bbox: BBox
    confidence: float
    label: str
    category: str
    is_person: bool = False
    track_id: Optional[int] = None

    @property
    def area(self) -> float:
        return self.bbox.area

    def with_track(self, track_id: int) -> "Detection":
        return Detection(
            bbox=self.bbox,
            confidence=self.confidence,
            label=self.label,
            category=self.category,
            is_person=self.is_person,
            track_id=track_id,
        )


@dataclass
class TrackFeature:
    """Aggregated view of a track, used by the association engine."""

    track_id: int
    bbox: BBox
    is_person: bool
    category: str
    confidence: float
    frames_seen: int = 1
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    velocity: tuple[float, float] = (0.0, 0.0)
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    face_score: Optional[float] = None

    @property
    def age_seconds(self) -> float:
        if not self.first_seen or not self.last_seen:
            return 0.0
        return max(0.0, (self.last_seen - self.first_seen).total_seconds())

    @property
    def stale_seconds(self) -> float:
        if not self.last_seen:
            return 0.0
        return max(0.0, (datetime.now(self.last_seen.tzinfo) - self.last_seen).total_seconds())


@dataclass
class FrameResult:
    """Everything one processed frame produced (used by the live view)."""

    detections: list[Detection] = field(default_factory=list)
    person_tracks: list[TrackFeature] = field(default_factory=list)
    equipment_tracks: list[TrackFeature] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
