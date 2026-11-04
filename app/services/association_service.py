"""Person <-> equipment association.

Proximity alone is *not* proof that somebody carries an item. The engine
combines four weak signals and returns an explicit quality level so the
dashboard can mark ambiguous cases for human review instead of guessing:

* carry overlap   - the object's centre is inside the person's expanded box
* proximity       - centre distance relative to the person's size
* temporal overlap - both tracks have been stable for several frames
* direction       - both are heading the same way (e.g. towards the exit)

When two people explain an object almost equally well, the result is
``AMBIGUOUS`` and ``requires_review`` is set. When nothing explains the object
well enough, no person is attached at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from app.models.enums import AssociationQuality, Direction
from app.services.types import BBox, TrackFeature

logger = logging.getLogger(__name__)


@dataclass
class AssociationEvidence:
    object_track_id: int
    category: str
    person_track_id: Optional[int]
    person_employee_id: Optional[int]
    person_employee_name: Optional[str]
    face_score: Optional[float]
    score: float
    quality: AssociationQuality
    requires_review: bool
    reasons: list[str] = field(default_factory=list)
    detection_confidence: float = 0.0

    @property
    def is_confident(self) -> bool:
        return self.quality is AssociationQuality.STRONG and not self.requires_review


@dataclass
class AssociationConfig:
    min_score: float = 0.45
    strong_score: float = 0.65
    ambiguity_margin: float = 0.12
    proximity_ratio: float = 1.10
    carry_ratio: float = 0.22
    min_track_frames: int = 4
    max_track_age_seconds: float = 12.0


def _normalise_velocity(track: TrackFeature) -> tuple[float, float]:
    vx, vy = track.velocity
    norm = (vx * vx + vy * vy) ** 0.5
    if norm < 1e-6:
        return (0.0, 0.0)
    return (vx / norm, vy / norm)


def _direction_agreement(person: TrackFeature, obj: TrackFeature) -> float:
    pv = _normalise_velocity(person)
    ov = _normalise_velocity(obj)
    if pv == (0.0, 0.0) or ov == (0.0, 0.0):
        return 0.5  # neutral
    dot = pv[0] * ov[0] + pv[1] * ov[1]
    return max(0.0, min(1.0, (dot + 1.0) / 2.0))


def _carry_score(person: TrackFeature, obj: TrackFeature, carry_ratio: float) -> float:
    person_box = person.bbox.expanded(carry_ratio)
    obj_box = obj.bbox
    if person_box.contains_point(obj_box.center):
        return 1.0
    # Partial overlap (object held at the side of the body).
    overlap = person_box.iou(obj_box)
    if overlap > 0:
        return min(0.9, 0.4 + overlap * 2.0)
    return 0.0


def _proximity_score(person: TrackFeature, obj: TrackFeature, proximity_ratio: float) -> float:
    distance = person.bbox.center_distance(obj.bbox)
    reference = max(1.0, person.bbox.diagonal * proximity_ratio)
    ratio = distance / reference
    if ratio <= 1.0:
        return 1.0
    if ratio >= 2.5:
        return 0.0
    return max(0.0, 1.0 - (ratio - 1.0) / 1.5)


def _temporal_score(person: TrackFeature, obj: TrackFeature, min_frames: int) -> float:
    frames = min(person.frames_seen, obj.frames_seen)
    if frames >= min_frames * 3:
        return 1.0
    return max(0.0, min(1.0, frames / float(max(1, min_frames * 3))))


class AssociationEngine:
    """Scores every (person, object) pair and keeps uncertainty visible."""

    def __init__(self, config: Optional[AssociationConfig] = None) -> None:
        self.config = config or AssociationConfig()

    def _score_pair(self, person: TrackFeature, obj: TrackFeature) -> tuple[float, list[str]]:
        cfg = self.config
        reasons: list[str] = []
        carry = _carry_score(person, obj, cfg.carry_ratio)
        proximity = _proximity_score(person, obj, cfg.proximity_ratio)
        temporal = _temporal_score(person, obj, cfg.min_track_frames)
        direction = _direction_agreement(person, obj)

        score = 0.50 * carry + 0.25 * proximity + 0.15 * temporal + 0.10 * direction
        if carry >= 0.9:
            reasons.append("obyekt tana hududida")
        elif proximity > 0.6:
            reasons.append("yaqin joylashgan")
        if temporal < 1.0:
            reasons.append("trek hali qisqa")
        if direction < 0.4:
            reasons.append("harakat yo‘nalishi mos emas")
        return round(score, 3), reasons

    def associate(
        self,
        person_tracks: Sequence[TrackFeature],
        equipment_tracks: Sequence[TrackFeature],
        *,
        direction: Direction = Direction.NONE,
    ) -> list[AssociationEvidence]:
        cfg = self.config
        results: list[AssociationEvidence] = []

        for obj in equipment_tracks:
            scored = sorted(
                ((self._score_pair(person, obj), person) for person in person_tracks),
                key=lambda item: item[0][0],
                reverse=True,
            )

            if not scored or scored[0][0][0] < cfg.min_score:
                results.append(
                    AssociationEvidence(
                        object_track_id=obj.track_id,
                        category=obj.category,
                        person_track_id=None,
                        person_employee_id=None,
                        person_employee_name=None,
                        face_score=None,
                        score=0.0,
                        quality=AssociationQuality.NONE,
                        requires_review=True if direction is not Direction.NONE else False,
                        reasons=["ishonchli bog‘lanish topilmadi"],
                        detection_confidence=obj.confidence,
                    )
                )
                continue

            (best_score, best_reasons), best_person = scored[0]
            second_score = scored[1][0][0] if len(scored) > 1 else 0.0
            ambiguous = (second_score > 0) and (best_score - second_score) < cfg.ambiguity_margin
            weak_track = min(best_person.frames_seen, obj.frames_seen) < cfg.min_track_frames

            if ambiguous:
                quality = AssociationQuality.AMBIGUOUS
                reasons = best_reasons + ["bir nechta shaxs yaqin - noaniq"]
            elif best_score >= cfg.strong_score and not weak_track:
                quality = AssociationQuality.STRONG
                reasons = best_reasons
            else:
                quality = AssociationQuality.WEAK
                reasons = best_reasons + (["trek juda qisqa"] if weak_track else [])

            requires_review = quality in {AssociationQuality.AMBIGUOUS, AssociationQuality.WEAK} or weak_track

            results.append(
                AssociationEvidence(
                    object_track_id=obj.track_id,
                    category=obj.category,
                    person_track_id=best_person.track_id,
                    person_employee_id=best_person.employee_id,
                    person_employee_name=best_person.employee_name,
                    face_score=best_person.face_score,
                    score=best_score,
                    quality=quality,
                    requires_review=requires_review,
                    reasons=reasons,
                    detection_confidence=obj.confidence,
                )
            )
        return results


def best_candidate(evidence: Iterable[AssociationEvidence], object_track_id: int) -> Optional[AssociationEvidence]:
    for item in evidence:
        if item.object_track_id == object_track_id:
            return item
    return None


def is_ambiguous(evidence: AssociationEvidence) -> bool:
    return evidence.quality in {AssociationQuality.AMBIGUOUS, AssociationQuality.WEAK}


def object_near_person(person: TrackFeature, obj: TrackFeature, ratio: float = 1.0) -> bool:
    """Simple helper used by tests and scripts."""
    return person.bbox.expanded(ratio).iou(obj.bbox) > 0 or person.bbox.center_distance(obj.bbox) < person.bbox.diagonal * ratio


def bbox_list(nested: Iterable[Iterable[float]]) -> list[BBox]:
    return [BBox(*[float(v) for v in item][:4]) for item in nested]
