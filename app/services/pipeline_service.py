"""Movement analysis pipeline (per camera).

Two layers:

* ``FrameAnalyzer`` - pure, synchronous per-frame reasoning (detection ->
  tracking -> identity -> line crossing -> association). No threads, no
  database: easy to unit test with synthetic frames.
* ``CameraPipeline`` - one worker thread per camera that pulls frames from the
  capture thread, runs the analyzer, persists events and publishes the
  annotated frame for the MJPEG live view.

Nothing in this module invents identities or movements. Every candidate event
carries its confidences and is written as PENDING until a human confirms it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Callable, Optional

import numpy as np

from app.config import Settings, get_settings
from app.models.enums import (
    AssociationQuality,
    Direction,
    EventType,
    VerificationStatus,
)
from app.services import asset_monitor, evidence_service, event_service, overlay
from app.services.association_service import AssociationEngine
from app.services.camera_service import CameraConfig, CameraWorker
from app.services.face_service import FaceService, expand_person_crop, get_face_service
from app.services.tracking_service import LineCrossingDetector, SimpleTracker, TrackMemory
from app.services.types import BBox, Detection, TrackFeature
from app.services.yolo_service import YoloService
from app.utils.time_utils import utc_now

logger = logging.getLogger(__name__)


@dataclass
class PersonCrossing:
    track_id: int
    direction: Direction
    employee_id: Optional[int]
    employee_name: Optional[str]
    face_score: Optional[float]
    track: TrackFeature


@dataclass
class MovementCandidate:
    object_track_id: int
    category: str
    direction: Direction
    person_track_id: Optional[int]
    employee_id: Optional[int]
    employee_name: Optional[str]
    face_score: Optional[float]
    association_score: float
    quality: AssociationQuality
    requires_review: bool
    detection_confidence: float
    reasons: list[str] = field(default_factory=list)
    # How long the object had been in this person's hands before the crossing.
    held_since: Optional[datetime] = None
    held_seconds: float = 0.0


@dataclass
class Possession:
    """One object currently held by one person.

    A possession starts when the association stays confident for several
    consecutive frames and ends only when the object is visibly left behind
    (put down) or when the track disappears (carried out of the frame).
    """

    object_track_id: int
    category: str
    person_track_id: int
    started_at: datetime
    last_confirmed_at: datetime
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    face_score: Optional[float] = None
    detection_confidence: float = 0.0
    association_score: float = 0.0
    quality: AssociationQuality = AssociationQuality.NONE
    requires_review: bool = True

    @property
    def held_seconds(self) -> float:
        return max(0.0, (self.last_confirmed_at - self.started_at).total_seconds())


@dataclass
class PossessionEvent:
    """Lifecycle fact: an object was picked up or put down."""

    action: str  # "pickup" | "putdown"
    object_track_id: int
    category: str
    person_track_id: Optional[int]
    employee_id: Optional[int]
    employee_name: Optional[str]
    face_score: Optional[float]
    detection_confidence: Optional[float]
    association_score: Optional[float]
    quality: AssociationQuality
    requires_review: bool
    occurred_at: datetime
    held_seconds: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    tracks: list[TrackFeature]
    crossings: list[PersonCrossing]
    movements: list[MovementCandidate]
    possessions: list[PossessionEvent] = field(default_factory=list)
    notes: str = ""


class SimulatedDetector:
    """Uses the simulation scene's ground truth instead of a YOLO model."""

    def __init__(self, worker: CameraWorker) -> None:
        self.worker = worker

    def detect(self, frame: np.ndarray, **_: object) -> list[Detection]:
        detections: list[Detection] = []
        for item in self.worker.simulation_ground_truth():
            detections.append(
                Detection(
                    bbox=BBox(*[float(v) for v in item["bbox"]]),
                    confidence=float(item["confidence"]),
                    label=str(item["label"]),
                    category=str(item["category"]),
                    is_person=bool(item["is_person"]),
                    track_id=int(item["track_id"]),
                )
            )
        return detections


class FrameAnalyzer:
    """Per-frame reasoning for one camera."""

    def __init__(
        self,
        config: CameraConfig,
        settings: Optional[Settings] = None,
        *,
        detector=None,
        face_service: Optional[FaceService] = None,
        association_engine: Optional[AssociationEngine] = None,
    ) -> None:
        self.config = config
        self.settings = settings or get_settings()
        self.detector = detector
        self.face_service = face_service
        self.association = association_engine or AssociationEngine()
        self.tracker = SimpleTracker()
        self.memory = TrackMemory()
        line = config.line
        self.person_line = (
            LineCrossingDetector(
                line,
                cooldown_seconds=self.settings.event_cooldown_seconds,
                flip=config.flip_line,
            )
            if line
            else None
        )
        self.equipment_line = (
            LineCrossingDetector(
                line,
                cooldown_seconds=self.settings.event_cooldown_seconds,
                flip=config.flip_line,
                min_track_frames=3,
            )
            if line
            else None
        )
        self._carry: dict[int, dict[int, int]] = defaultdict(dict)  # object -> {person: frames}
        self._movement_log: dict[int, datetime] = {}
        self._last_seen: dict[int, datetime] = {}
        self.min_carry_frames = 3
        self.stale_seconds = 2.0
        # Possession: which object is currently in whose hands, and for how long.
        self._possession: dict[int, Possession] = {}
        self._possession_streak: dict[int, tuple[int, int, datetime]] = {}
        self.possession_min_frames = 3
        self.possession_grace_seconds = 6.0
        # Body zone that still counts as "held" (a laptop can be carried at
        # arm's length, so it is wider than the association carry ratio).
        self.hold_expansion = 0.35
        # A carried object often leaves the frame before its holder reaches the
        # line: hidden behind the body, packed in a bag, out of the camera view.
        # The binding is remembered per person for this long so the exit event
        # still names the equipment and its time instead of disappearing.
        self.carry_memory_seconds = 45.0
        self._carry_memory: dict[int, Possession] = {}
        self.frames_processed = 0
        self.detection_errors = 0

    # -- helpers ---------------------------------------------------------
    def _detect(self, frame: np.ndarray) -> list[Detection]:
        if self.detector is None:
            return []
        return self.detector.detect(frame, confidence=self.config.confidence)

    def _recognise(self, frame: np.ndarray, track: TrackFeature) -> None:
        if self.face_service is None or not self.face_service.available:
            return
        if self.face_service.gallery.size == 0:
            return
        if not self.memory.should_try_face(track.track_id, self.settings.face_recognition_interval):
            return
        crop = expand_person_crop(frame, track.bbox)
        self.memory.mark_face_attempt(track.track_id)
        if crop is None:
            return
        try:
            match = self.face_service.recognize(crop)
        except Exception as exc:  # pragma: no cover - model dependent
            logger.debug("Yuz tanishda xatolik (trek %s): %s", track.track_id, exc)
            return
        if match and match.matched and match.employee_id:
            self.memory.assign_employee(track.track_id, match.employee_id, match.employee_name or "", match.score)
            logger.info(
                "Trek %s xodim bilan bog‘landi: %s (%.3f)",
                track.track_id,
                match.employee_name,
                match.score,
            )

    def _update_carry_map(self, evidence) -> None:
        now = utc_now()
        for item in evidence:
            if item.person_track_id is None:
                continue
            if item.quality in {AssociationQuality.STRONG, AssociationQuality.WEAK}:
                bucket = self._carry[item.object_track_id]
                bucket[item.person_track_id] = bucket.get(item.person_track_id, 0) + 1
                self._last_seen[item.object_track_id] = now
        # Drop stale objects so an old binding cannot trigger a future movement.
        for object_id in list(self._carry):
            last = self._last_seen.get(object_id)
            if last is None or (now - last).total_seconds() > self.stale_seconds * 2:
                self._carry.pop(object_id, None)
                self._last_seen.pop(object_id, None)

    def _carried_objects(self, person_track_id: int) -> list[int]:
        now = utc_now()
        result: list[tuple[int, int]] = []
        for object_id, people in self._carry.items():
            count = people.get(person_track_id, 0)
            if count < self.min_carry_frames:
                continue
            last = self._last_seen.get(object_id)
            if last is not None and (now - last).total_seconds() > self.stale_seconds:
                continue
            result.append((object_id, count))
        result.sort(key=lambda item: item[1], reverse=True)
        return [object_id for object_id, _count in result[:3]]

    def _already_logged(self, object_track_id: int) -> bool:
        last = self._movement_log.get(object_track_id)
        if last is None:
            return False
        return (utc_now() - last).total_seconds() < self.settings.event_cooldown_seconds

    # -- possession lifecycle --------------------------------------------
    def _possession_event(
        self,
        action: str,
        held: Possession,
        evidence,
        now: datetime,
    ) -> PossessionEvent:
        reasons = list(evidence.reasons) if evidence is not None else []
        if held.held_seconds > 0:
            reasons.append(f"qo‘lda {held.held_seconds:.0f}s turdi")
        return PossessionEvent(
            action=action,
            object_track_id=held.object_track_id,
            category=held.category,
            person_track_id=held.person_track_id,
            employee_id=held.employee_id,
            employee_name=held.employee_name,
            face_score=held.face_score,
            detection_confidence=round(held.detection_confidence, 3),
            association_score=round(held.association_score, 3),
            quality=held.quality,
            requires_review=held.requires_review,
            occurred_at=held.started_at if action == "pickup" else now,
            held_seconds=round(held.held_seconds, 1),
            reasons=reasons,
        )

    def _holds(self, person: TrackFeature, obj: TrackFeature) -> bool:
        """Physical definition of "in the hands": the box overlaps the body.

        Proximity alone is not enough - somebody standing next to a rack is not
        carrying it. An object lying on the desk stops overlapping the body
        once the person steps away, which is exactly the put-down moment.
        """
        box = person.bbox.expanded(self.hold_expansion)
        return box.contains_point(obj.bbox.center) or box.iou(obj.bbox) > 0

    def _update_possessions(
        self,
        now: datetime,
        person_tracks: list[TrackFeature],
        equipment_tracks: list[TrackFeature],
        evidence,
    ) -> list[PossessionEvent]:
        """Bind every held object to one person until it is put down again.

        ``pickup`` is emitted once the link survives ``possession_min_frames``
        consecutive frames; the binding then survives short detection gaps and
        ends with ``putdown`` only when the object stays visible without a
        holder. If the track disappears instead (carried out of the frame) no
        put-down is invented - the exit movement already covers that case.
        """
        events: list[PossessionEvent] = []
        by_object = {item.object_track_id: item for item in evidence}
        people = {track.track_id: track for track in person_tracks}
        visible: set[int] = set()

        for obj in equipment_tracks:
            visible.add(obj.track_id)
            match = by_object.get(obj.track_id)
            person = people.get(match.person_track_id) if match and match.person_track_id is not None else None
            if person is not None and match.score < self.association.config.min_score:
                person = None
            if person is not None and not self._holds(person, obj):
                person = None
            held = self._possession.get(obj.track_id)

            if person is not None:
                if held is None:
                    frames, since = 1, now
                    streak = self._possession_streak.get(obj.track_id)
                    if streak is not None and streak[0] == person.track_id:
                        frames, since = streak[1] + 1, streak[2]
                    self._possession_streak[obj.track_id] = (person.track_id, frames, since)
                    if frames < self.possession_min_frames:
                        continue
                    held = Possession(
                        object_track_id=obj.track_id,
                        category=obj.category,
                        person_track_id=person.track_id,
                        started_at=since,
                        last_confirmed_at=now,
                        employee_id=person.employee_id,
                        employee_name=person.employee_name,
                        face_score=person.face_score,
                        detection_confidence=obj.confidence,
                        association_score=match.score,
                        quality=match.quality,
                        requires_review=match.requires_review or person.employee_id is None,
                    )
                    self._possession[obj.track_id] = held
                    self._possession_streak.pop(obj.track_id, None)
                    self._remember_carry(held)
                    events.append(self._possession_event("pickup", held, match, now))
                    continue

                held.last_confirmed_at = now
                held.category = obj.category
                held.detection_confidence = obj.confidence
                held.association_score = match.score
                held.quality = match.quality
                held.person_track_id = person.track_id
                if person.employee_id is not None and (
                    held.employee_id is None or (person.face_score or 0.0) > (held.face_score or 0.0)
                ):
                    held.employee_id = person.employee_id
                    held.employee_name = person.employee_name
                    held.face_score = person.face_score
                held.requires_review = match.requires_review or held.employee_id is None
                self._remember_carry(held)
                continue

            self._possession_streak.pop(obj.track_id, None)
            if held is None:
                continue
            if (now - held.last_confirmed_at).total_seconds() >= self.possession_grace_seconds:
                events.append(self._possession_event("putdown", held, None, now))
                self._possession.pop(obj.track_id, None)
                self._forget_carry(held)

        # Tracks that vanished: keep the binding during the grace period, then
        # forget it silently (the object left the scene with its holder).
        for object_id in list(self._possession):
            if object_id in visible:
                continue
            held = self._possession[object_id]
            if (now - held.last_confirmed_at).total_seconds() >= self.possession_grace_seconds:
                self._possession.pop(object_id, None)
        # The remembered binding is a hint, not a fact: it expires on its own.
        cutoff = now - timedelta(seconds=self.carry_memory_seconds)
        for person_id in [
            key for key, item in self._carry_memory.items() if item.last_confirmed_at < cutoff
        ]:
            self._carry_memory.pop(person_id, None)
        return events

    def _remember_carry(self, held: Possession) -> None:
        """Snapshot the binding so it survives the object leaving the frame."""
        self._carry_memory[held.person_track_id] = replace(held)

    def _forget_carry(self, held: Possession) -> None:
        remembered = self._carry_memory.get(held.person_track_id)
        if remembered is None or remembered.object_track_id == held.object_track_id:
            self._carry_memory.pop(held.person_track_id, None)

    def _possessed_objects(self, person_track_id: int) -> list[Possession]:
        return [held for held in self._possession.values() if held.person_track_id == person_track_id]

    def active_possessions(self) -> list[Possession]:
        """Snapshot for the UI; the pipeline thread may be mid-update."""
        try:
            return list(self._possession.values())
        except RuntimeError:  # pragma: no cover - dict mutated by the frame thread
            return []

    # -- main entry point ------------------------------------------------
    def process(self, frame: np.ndarray) -> AnalysisResult:
        now = utc_now()
        self.frames_processed += 1
        try:
            detections = self._detect(frame)
        except Exception as exc:
            self.detection_errors += 1
            logger.warning("Deteksiya xatosi (%s): %s", self.config.code, exc)
            detections = []

        if detections and detections[0].track_id is None:
            detections = self.tracker.update(detections)

        states = self.memory.update(detections, now)
        self.memory.prune(now, max_age_seconds=30.0)

        person_states = [state for state in states if state.is_person]
        equipment_states = [state for state in states if not state.is_person]

        # Identity first: the crossing/movement records should carry the name
        # that was recognised in this very frame.
        for state in person_states:
            self._recognise(frame, state.feature())

        person_tracks = [state.feature() for state in person_states]
        equipment_tracks = [state.feature() for state in equipment_states]

        evidence = self.association.associate(person_tracks, equipment_tracks)
        self._update_carry_map(evidence)
        possession_events = self._update_possessions(now, person_tracks, equipment_tracks, evidence)

        crossings: list[PersonCrossing] = []
        movements: list[MovementCandidate] = []
        notes: list[str] = []

        # --- people crossing the entrance/exit line ----------------------
        if self.person_line is not None:
            for track in person_tracks:
                direction = self.person_line.update(
                    track.track_id,
                    track.bbox.bottom_center(),
                    now,
                    frames_seen=track.frames_seen,
                )
                if direction is None:
                    continue
                self.memory.record_crossing(track.track_id, now, direction)
                crossings.append(
                    PersonCrossing(
                        track_id=track.track_id,
                        direction=direction,
                        employee_id=track.employee_id,
                        employee_name=track.employee_name,
                        face_score=track.face_score,
                        track=track,
                    )
                )
                notes.append(f"#{track.track_id} {direction.label_uz}")
                if direction is Direction.NONE:
                    continue
                # Objects bound to this person are reported first: the binding
                # already proved who was holding them before the crossing.
                possessed = {held.object_track_id: held for held in self._possessed_objects(track.track_id)}
                for object_id, held in possessed.items():
                    if self._already_logged(object_id):
                        continue
                    item = self._build_movement(
                        object_id,
                        direction,
                        track,
                        equipment_tracks,
                        evidence,
                        held=held,
                    )
                    if item is not None:
                        movements.append(item)
                        self._movement_log[object_id] = now
                for object_id in self._carried_objects(track.track_id):
                    if object_id in possessed or self._already_logged(object_id):
                        continue
                    item = self._build_movement(
                        object_id,
                        direction,
                        track,
                        equipment_tracks,
                        evidence,
                    )
                    if item is not None:
                        movements.append(item)
                        self._movement_log[object_id] = now
                # The equipment may already be out of the frame (hidden behind
                # the body, inside a bag). The remembered binding keeps the
                # exit/entry event tied to its holder and to the pick-up time.
                remembered = self._carry_memory.get(track.track_id)
                if remembered is not None and remembered.object_track_id not in possessed:
                    object_id = remembered.object_track_id
                    if not self._already_logged(object_id):
                        item = self._build_movement(
                            object_id,
                            direction,
                            track,
                            equipment_tracks,
                            evidence,
                            held=remembered,
                        )
                        if item is not None:
                            item.requires_review = True
                            item.reasons.append("jihoz kadrda ko‘rinmadi (biriktirish xotirasi)")
                            movements.append(item)
                            self._movement_log[object_id] = now
                            notes.append(
                                f"#{object_id} {remembered.category} {direction.label_uz}"
                            )
                    self._carry_memory.pop(track.track_id, None)

        # --- equipment crossing the line without a detected person crossing
        if self.equipment_line is not None:
            for obj in equipment_tracks:
                direction = self.equipment_line.update(
                    obj.track_id,
                    obj.bbox.center,
                    now,
                    frames_seen=obj.frames_seen,
                )
                if direction is None or self._already_logged(obj.track_id):
                    continue
                item = self._build_movement(
                    obj.track_id,
                    direction,
                    None,
                    equipment_tracks,
                    evidence,
                )
                if item is not None:
                    movements.append(item)
                    self._movement_log[obj.track_id] = now
                    notes.append(f"#{obj.track_id} {obj.category} {direction.label_uz}")

        self._cleanup_log()

        for item in possession_events:
            verb = "olindi" if item.action == "pickup" else "qo‘yildi"
            notes.append(f"{item.category} {verb}")

        return AnalysisResult(
            tracks=person_tracks + equipment_tracks,
            crossings=crossings,
            movements=movements,
            possessions=possession_events,
            notes=", ".join(notes),
        )

    def _cleanup_log(self) -> None:
        cutoff = utc_now() - timedelta(seconds=max(60, self.settings.event_cooldown_seconds * 3))
        for object_id in [oid for oid, moment in self._movement_log.items() if moment < cutoff]:
            self._movement_log.pop(object_id, None)

    def _build_movement(
        self,
        object_track_id: int,
        direction: Direction,
        person_track: Optional[TrackFeature],
        equipment_tracks: list[TrackFeature],
        evidence,
        held: Optional[Possession] = None,
    ) -> Optional[MovementCandidate]:
        obj = next((item for item in equipment_tracks if item.track_id == object_track_id), None)
        if obj is None and held is None:
            return None
        if obj is not None and obj.confidence <= 0:
            return None
        match = next((item for item in evidence if item.object_track_id == object_track_id), None)

        reasons: list[str] = []
        person_track_id = match.person_track_id if match else None
        score = match.score if match else 0.0
        quality = match.quality if match else AssociationQuality.NONE
        requires_review = True
        employee_id = None
        employee_name = None
        face_score = None
        held_since: Optional[datetime] = None
        held_seconds = 0.0

        if match is not None:
            reasons = list(match.reasons)
            employee_id = match.person_employee_id
            employee_name = match.person_employee_name
            face_score = match.face_score
            requires_review = match.requires_review

        if person_track is not None:
            # The carrying person is the one who crossed the line, not just the
            # closest box - this overrides proximity-only evidence.
            person_track_id = person_track.track_id
            employee_id = person_track.employee_id
            employee_name = person_track.employee_name
            face_score = person_track.face_score
            if match is not None and match.person_track_id != person_track.track_id:
                requires_review = True
                reasons.append("bog‘lanish shu shaxsga mos emas")
            elif match is not None and match.quality is AssociationQuality.AMBIGUOUS:
                requires_review = True

        if held is not None:
            # The possession record is the authority on *who* held the object
            # and *since when*; the crossing frame only refreshes the identity.
            person_track_id = held.person_track_id
            if employee_id is None and held.employee_id is not None:
                employee_id = held.employee_id
                employee_name = held.employee_name
                face_score = held.face_score
            if match is None or held.association_score > score:
                score = held.association_score
                quality = held.quality
            held_since = held.started_at
            held_seconds = held.held_seconds
            reasons.append(f"qo‘lda {held_seconds:.0f}s")

        if employee_id is None:
            requires_review = True
            reasons.append("xodim ishonchli aniqlanmadi")
        elif held is not None and held.quality is AssociationQuality.STRONG:
            requires_review = False

        return MovementCandidate(
            object_track_id=object_track_id,
            category=obj.category if obj is not None else held.category,
            direction=direction,
            person_track_id=person_track_id,
            employee_id=employee_id,
            employee_name=employee_name,
            face_score=face_score,
            association_score=round(float(score), 3),
            quality=quality,
            requires_review=requires_review,
            detection_confidence=float(obj.confidence) if obj is not None else held.detection_confidence,
            reasons=reasons,
            held_since=held_since,
            held_seconds=held_seconds,
        )


class CameraPipeline:
    """Threaded per-camera pipeline: frames in, events out."""

    def __init__(
        self,
        worker: CameraWorker,
        config: CameraConfig,
        settings: Optional[Settings] = None,
        *,
        detector: Optional[YoloService] = None,
        face_service: Optional[FaceService] = None,
        session_scope: Optional[Callable] = None,
    ) -> None:
        self.worker = worker
        self.config = config
        self.settings = settings or get_settings()
        self.detector = detector
        self.face_service = face_service or get_face_service(self.settings)

        if session_scope is None:
            from app.database import session_scope as db_session_scope

            session_scope = db_session_scope
        self.session_scope = session_scope

        simulated = config.source_type == "simulation" or self.settings.simulation_mode
        if simulated:
            effective_detector = SimulatedDetector(worker)
        else:
            effective_detector = detector

        self.analyzer = FrameAnalyzer(
            config,
            self.settings,
            detector=effective_detector,
            face_service=self.face_service,
        )
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_analysis: Optional[AnalysisResult] = None
        self.events_created = 0
        self.processing_errors = 0

        # QR inventory: opt-in. ``None`` means the previous behaviour is kept.
        self.asset_monitor = asset_monitor.build(self.config.code, self.session_scope, self.settings)
        if self.asset_monitor is not None:
            logger.info(
                "QR kuzatuvi yoqildi: kamera %s (har %.1f s)",
                self.config.code,
                self.asset_monitor.interval,
            )

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"pipeline-{self.config.code}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def health(self) -> dict:
        return {
            "code": self.config.code,
            "running": self.is_running,
            "events_created": self.events_created,
            "processing_errors": self.processing_errors,
            "frames_processed": self.analyzer.frames_processed,
            "detection_errors": self.analyzer.detection_errors,
            "tracks": len(self.analyzer.memory.states),
        }

    # -- loop ------------------------------------------------------------
    def _run(self) -> None:
        logger.info("Pipeline ishga tushdi: %s", self.config.code)
        while not self._stop.is_set():
            frame = self.worker.drain()
            if frame is None:
                if self._stop.wait(0.05):
                    break
                continue
            try:
                result = self.analyzer.process(frame)
                self.last_analysis = result
                annotated = overlay.annotate(
                    frame,
                    tracks=result.tracks,
                    line=self.config.line,
                    camera_name=self.config.name,
                    status=self.worker.status,
                    notes=result.notes,
                )
                self.worker.publish_annotated(annotated)
                self._persist(result.crossings, result.movements, result.possessions, annotated)
                if self.asset_monitor is not None:
                    # Reads device labels on the same frame; failures stay inside.
                    self.asset_monitor.observe(frame)
            except Exception:  # pragma: no cover - defensive: keep the camera alive
                self.processing_errors += 1
                logger.exception("Pipeline xatosi (%s)", self.config.code)
                time.sleep(0.2)
        logger.info("Pipeline to‘xtadi: %s", self.config.code)

    # -- persistence -----------------------------------------------------
    def _persist(
        self,
        crossings: list[PersonCrossing],
        movements: list[MovementCandidate],
        possessions: list[PossessionEvent],
        annotated: np.ndarray,
    ) -> None:
        if not crossings and not movements and not possessions:
            return
        try:
            with self.session_scope() as session:
                for crossing in crossings:
                    self._save_crossing(session, crossing, annotated)
                for possession in possessions:
                    self._save_possession(session, possession, annotated)
                for movement in movements:
                    self._save_movement(session, movement, annotated)
        except Exception:  # pragma: no cover - DB dependent
            self.processing_errors += 1
            logger.exception("Hodisalarni saqlashda xatolik (%s)", self.config.code)

    def _save_crossing(self, session, crossing: PersonCrossing, annotated: np.ndarray) -> None:
        known = crossing.employee_id is not None
        if known:
            event_type = EventType.PERSON_ENTERED if crossing.direction is Direction.IN else EventType.PERSON_EXITED
            status = VerificationStatus.AUTO
        else:
            event_type = (
                EventType.UNKNOWN_PERSON_ENTERED if crossing.direction is Direction.IN else EventType.UNKNOWN_PERSON_EXITED
            )
            status = VerificationStatus.PENDING

        event, created = event_service.create_event(
            session,
            event_type=event_type,
            camera_code=self.config.code,
            employee_id=crossing.employee_id,
            person_track_id=crossing.track_id,
            direction=crossing.direction,
            detection_confidence=round(crossing.track.confidence, 3),
            face_match_score=crossing.face_score,
            verification_status=status,
            notes=None if known else "Yuz ishonchli tanilmadi - tekshirish talab qilinadi.",
        )
        if not created:
            return
        path = evidence_service.save_evidence(annotated, event.public_id, settings=self.settings)
        if path:
            event.evidence_path = path
        self.events_created += 1
        logger.info(
            "Kirish/chiqish hodisasi: %s %s %s",
            crossing.direction.value,
            crossing.employee_name or "Noma'lum shaxs",
            event.public_id,
        )

    def _save_possession(self, session, possession: PossessionEvent, annotated: np.ndarray) -> None:
        """Pick-up / put-down is an observation, never a ledger movement."""
        event_type = (
            EventType.EQUIPMENT_PICKED_UP if possession.action == "pickup" else EventType.EQUIPMENT_PUT_DOWN
        )
        notes = "; ".join(possession.reasons) if possession.reasons else None
        event, created = event_service.create_event(
            session,
            event_type=event_type,
            camera_code=self.config.code,
            occurred_at=possession.occurred_at,
            employee_id=possession.employee_id,
            person_track_id=possession.person_track_id,
            equipment_category=possession.category,
            object_track_id=possession.object_track_id,
            detection_confidence=possession.detection_confidence,
            face_match_score=possession.face_score,
            association_confidence=possession.association_score,
            verification_status=VerificationStatus.AUTO,
            notes=notes,
            extra={
                "association_quality": possession.quality.value,
                "action": possession.action,
                "held_seconds": possession.held_seconds,
                "requires_review": possession.requires_review,
            },
            # One second granularity: several objects can be picked up together.
            cooldown_seconds=1,
        )
        if not created:
            return
        path = evidence_service.save_evidence(annotated, event.public_id, settings=self.settings)
        if path:
            event.evidence_path = path
        self.events_created += 1
        logger.info(
            "Jihoz biriktirish: %s %s -> %s %s",
            possession.category,
            possession.action,
            possession.employee_name or "Noma'lum shaxs",
            event.public_id,
        )

    def _save_movement(self, session, movement: MovementCandidate, annotated: np.ndarray) -> None:
        event_type = (
            EventType.EQUIPMENT_BROUGHT_IN
            if movement.direction is Direction.IN
            else EventType.EQUIPMENT_TAKEN_OUT
        )
        notes = "; ".join(movement.reasons) if movement.reasons else None
        if movement.requires_review:
            notes = (notes + " | " if notes else "") + "Tekshirish kerak"
        event, created = event_service.create_event(
            session,
            event_type=event_type,
            camera_code=self.config.code,
            employee_id=movement.employee_id,
            person_track_id=movement.person_track_id,
            equipment_category=movement.category,
            object_track_id=movement.object_track_id,
            direction=movement.direction,
            detection_confidence=round(movement.detection_confidence, 3),
            face_match_score=movement.face_score,
            association_confidence=movement.association_score,
            verification_status=VerificationStatus.PENDING,
            notes=notes,
            extra={
                "association_quality": movement.quality.value,
                "requires_review": movement.requires_review,
                "held_since": movement.held_since.isoformat() if movement.held_since else None,
                "held_seconds": movement.held_seconds,
            },
        )
        if not created:
            return
        path = evidence_service.save_evidence(annotated, event.public_id, settings=self.settings)
        if path:
            event.evidence_path = path
        self.events_created += 1
        logger.info(
            "Jihoz harakati: %s %s (bog‘lanish %.2f, %s) %s",
            movement.category,
            movement.direction.value,
            movement.association_score,
            movement.quality.value,
            event.public_id,
        )
