"""YOLO detection/tracking wrapper (Ultralytics).

The model is loaded **once** per process. When Ultralytics or the weights are
missing the service reports ``available=False`` with a human readable reason;
it never fabricates detections.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

import numpy as np

from app.config import Settings, get_settings
from app.services.types import BBox, Detection

logger = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the optional AI stack
    from ultralytics import YOLO  # type: ignore

    _ULTRALYTICS_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # pragma: no cover
    YOLO = None  # type: ignore
    _ULTRALYTICS_IMPORT_ERROR = f"ultralytics o‘rnatilmagan: {exc}"


@dataclass(frozen=True)
class CategoryRule:
    code: str
    label_uz: str
    is_person: bool
    min_confidence: Optional[float] = None


def build_rules(categories: Iterable) -> dict[str, CategoryRule]:
    """Map every configured YOLO label (lowercase) to a business category."""
    rules: dict[str, CategoryRule] = {}
    for category in categories:
        rule = CategoryRule(
            code=category.code,
            label_uz=category.label_uz,
            is_person=bool(category.is_person),
            min_confidence=category.min_confidence,
        )
        for label in category.yolo_labels or [category.code]:
            rules[str(label).strip().lower()] = rule
    return rules


@dataclass(frozen=True)
class ShapeRule:
    """What a category's box is allowed to look like.

    A general model happily reports a mouse on the desk as ``cell phone`` or a
    hand next to the ear as a phone. Confidence alone cannot separate those, so
    every handheld category also has to pass a geometric sanity check: phones
    are tall-ish and small, mice and keyboards are wide and flat.
    """

    aspect: tuple[float, float]  # width / height
    min_area_ratio: float = 0.0  # of the whole frame
    max_area_ratio: float = 1.0


SHAPE_RULES: dict[str, ShapeRule] = {
    "smartphone": ShapeRule((0.28, 1.25), 0.0012, 0.10),
    "tablet": ShapeRule((0.45, 1.90), 0.0020, 0.15),
    "ip_phone": ShapeRule((0.30, 1.10), 0.0012, 0.10),
    "landline_phone": ShapeRule((0.30, 1.30), 0.0015, 0.12),
    "mouse": ShapeRule((0.45, 2.60), 0.0008, 0.20),
    "keyboard": ShapeRule((1.60, 6.00), 0.0020, 0.20),
    "flash_drive": ShapeRule((0.20, 3.00), 0.0006, 0.05),
    "external_hdd": ShapeRule((0.40, 2.50), 0.0010, 0.08),
    "camera": ShapeRule((0.40, 2.00), 0.0008, 0.10),
    "mini_camera": ShapeRule((0.40, 2.00), 0.0006, 0.08),
    "lan_tester": ShapeRule((0.25, 3.00), 0.0008, 0.08),
    "lan_cable": ShapeRule((0.05, 8.00), 0.0008, 0.30),
}

# Handsets are the usual victims of desk-hardware confusion.
DEVICE_CODES = {"smartphone", "tablet", "ip_phone", "landline_phone"}
DESK_CODES = {"mouse", "keyboard"}


def plausible_box(code: str, bbox: BBox, frame_shape: tuple[int, ...]) -> bool:
    """Reject boxes whose shape or size cannot belong to the category."""
    rule = SHAPE_RULES.get(code)
    if rule is None:
        return True
    if bbox.width <= 0 or bbox.height <= 0:
        return False
    ratio = bbox.width / bbox.height
    if not (rule.aspect[0] <= ratio <= rule.aspect[1]):
        return False
    frame_area = max(1.0, float(frame_shape[0]) * float(frame_shape[1]))
    return rule.min_area_ratio <= bbox.area / frame_area <= rule.max_area_ratio


def resolve_confusions(detections: list[Detection], iou_threshold: float = 0.35) -> list[Detection]:
    """Drop a handset label that sits on top of a mouse/keyboard box.

    A desk with a mouse and a keyboard is a stable scene. When the model also
    reports a phone exactly there, it is the same object seen twice under a
    wrong class, so the wider desk hardware wins.
    """
    desk = [item for item in detections if item.category in DESK_CODES]
    if not desk:
        return list(detections)
    kept: list[Detection] = []
    for candidate in detections:
        if candidate.category in DEVICE_CODES and any(
            item.bbox.iou(candidate.bbox) >= iou_threshold for item in desk
        ):
            logger.debug("Chalkashtirish bartaraf etildi: %s (%.2f)", candidate.category, candidate.confidence)
            continue
        kept.append(candidate)
    return kept


def dedupe_overlaps(detections: list[Detection], iou_threshold: float = 0.6) -> list[Detection]:
    """Drop duplicate boxes that belong to *different* categories.

    One physical object is often reported under two labels (a desktop monitor as
    ``tv`` and ``monitor``, a phone as ``cell phone`` and ``remote``). Keeping
    both doubles the event stream and the ledger, so boxes with a high IoU keep
    only the most confident category.
    """
    if len(detections) < 2:
        return list(detections)
    threshold = max(0.1, min(0.95, float(iou_threshold)))
    ordered = sorted(detections, key=lambda item: item.confidence, reverse=True)
    kept: list[Detection] = []
    for candidate in ordered:
        for existing in kept:
            if existing.category != candidate.category and existing.bbox.iou(candidate.bbox) >= threshold:
                break
        else:
            kept.append(candidate)
    return kept


class YoloService:
    """Thread-safe, lazily loaded detector."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        rules: Optional[Mapping[str, CategoryRule]] = None,
        model_path: Optional[Path] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.rules: dict[str, CategoryRule] = dict(rules or {})
        self.model_path = Path(model_path or self.settings.yolo_model_path)
        self._model = None
        self._lock = threading.Lock()
        self._loaded_path: Optional[Path] = None
        self._load_error: Optional[str] = None
        self._using_fallback = False
        self._tracker_broken = False
        self.inference_count = 0
        self.last_inference_ms: Optional[float] = None
        self.last_class_names: list[str] = []

    # -- status ---------------------------------------------------------
    @property
    def available(self) -> bool:
        if _ULTRALYTICS_IMPORT_ERROR:
            return False
        return self._load_error is None

    @property
    def reason_unavailable(self) -> Optional[str]:
        if _ULTRALYTICS_IMPORT_ERROR:
            return _ULTRALYTICS_IMPORT_ERROR
        return self._load_error

    def status(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason_unavailable,
            "model_path": str(self.model_path),
            "loaded_path": str(self._loaded_path) if self._loaded_path else None,
            "using_fallback": self._using_fallback,
            "device": self.settings.yolo_device,
            "image_size": self.settings.yolo_image_size,
            "confidence": self.settings.yolo_confidence,
            "inference_count": self.inference_count,
            "last_inference_ms": self.last_inference_ms,
            "classes": self.last_class_names,
        }

    # -- model lifecycle -------------------------------------------------
    def _candidate_paths(self) -> list[Path]:
        candidates: list[Path] = []
        if self.model_path.exists():
            candidates.append(self.model_path)
        fallback = self.settings.yolo_fallback_model
        if fallback:
            candidates.append(Path(fallback))
        return candidates

    def ensure_loaded(self) -> bool:
        """Load the weights once; returns True when a model is usable."""
        if _ULTRALYTICS_IMPORT_ERROR:
            self._load_error = _ULTRALYTICS_IMPORT_ERROR
            return False
        if self._model is not None:
            return True
        with self._lock:
            if self._model is not None:
                return True
            for candidate in self._candidate_paths():
                try:
                    logger.info("YOLO model yuklanmoqda: %s", candidate)
                    model = YOLO(str(candidate))
                    self._model = model
                    self._loaded_path = candidate
                    self._using_fallback = candidate != self.model_path
                    self._tracker_broken = False
                    self._load_error = None
                    names = getattr(model, "names", {}) or {}
                    self.last_class_names = [str(v) for v in names.values()] if hasattr(names, "values") else []
                    if self._using_fallback:
                        logger.warning(
                            "Maxsus o‘qitilgan model topilmadi, umumiy model ishlatiladi: %s. "
                            "Bu model barcha ofis jihozlarini tanimaydi.",
                            candidate,
                        )
                    return True
                except Exception as exc:  # pragma: no cover - environment dependent
                    self._load_error = f"YOLO modelini yuklab bo‘lmadi ({candidate}): {exc}"
                    logger.error(self._load_error)
            return False

    def reload(self, rules: Optional[Mapping[str, CategoryRule]] = None) -> bool:
        with self._lock:
            self._model = None
            self._load_error = None
            self._using_fallback = False
            self._tracker_broken = False
        if rules is not None:
            self.rules = dict(rules)
        return self.ensure_loaded()

    # -- inference -------------------------------------------------------
    def _resolve(self, label: str) -> Optional[CategoryRule]:
        rule = self.rules.get(label.strip().lower())
        if rule is None:
            # Business categories can be used directly as labels as well.
            for candidate in self.rules.values():
                if candidate.code == label.strip().lower():
                    return candidate
        return rule

    def detect(
        self,
        frame: np.ndarray,
        *,
        confidence: Optional[float] = None,
        use_tracking: bool = True,
        allow_fallback_categories: bool = False,
    ) -> list[Detection]:
        """Run detection (and ByteTrack when available) on one BGR frame."""
        if not self.ensure_loaded() or self._model is None:
            raise ModelUnavailableError(self.reason_unavailable or "YOLO modeli mavjud emas")

        conf = self._inference_confidence(confidence)
        kwargs = dict(
            conf=conf,
            imgsz=self.settings.yolo_image_size,
            device=self.settings.yolo_device,
            verbose=False,
        )
        import time

        started = time.perf_counter()
        if use_tracking and not self._tracker_broken:
            try:
                results = self._model.track(
                    source=frame,
                    persist=True,
                    tracker=self.settings.yolo_tracker,
                    **kwargs,
                )
            except Exception as exc:  # pragma: no cover - depends on tracker files
                # Remember the failure: without this, every frame would retry the
                # tracker (and ultralytics would keep reinstalling its dependency).
                self._tracker_broken = True
                logger.warning(
                    "ByteTrack ishlamadi (%s), shu jarayonda oddiy deteksiya ishlatiladi", exc
                )
                results = self._model.predict(source=frame, **kwargs)
        else:
            results = self._model.predict(source=frame, **kwargs)
        self.last_inference_ms = round((time.perf_counter() - started) * 1000, 1)
        self.inference_count += 1

        detections: list[Detection] = []
        names = getattr(self._model, "names", {}) or {}
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None or len(boxes) == 0:
                continue
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            classes = boxes.cls.cpu().numpy().astype(int)
            ids = boxes.id.cpu().numpy().astype(int) if getattr(boxes, "id", None) is not None else None
            for index in range(len(classes)):
                label = str(names.get(int(classes[index]), classes[index])) if hasattr(names, "get") else str(classes[index])
                rule = self._resolve(label)
                if rule is None:
                    if not allow_fallback_categories:
                        continue
                    rule = CategoryRule(code="other_equipment", label_uz="Boshqa jihoz", is_person=False)
                min_conf = self._effective_min_confidence(rule, confidence)
                if float(confs[index]) < min_conf:
                    continue
                bbox = BBox(*[float(v) for v in xyxy[index][:4]])
                if not plausible_box(rule.code, bbox, frame.shape):
                    continue
                track_id = int(ids[index]) if ids is not None else None
                detections.append(
                    Detection(
                        bbox=bbox,
                        confidence=float(confs[index]),
                        label=label,
                        category=rule.code,
                        is_person=rule.is_person,
                        track_id=track_id,
                    )
                )
        return dedupe_overlaps(resolve_confusions(detections), self.settings.yolo_dedupe_iou)

    def _inference_confidence(self, override: Optional[float]) -> float:
        """Lowest confidence the model must report.

        Categories such as ``lan_cable`` or ``mini_camera`` need a lower floor
        than the global default, but ``conf`` passed to the model is a hard cut:
        boxes below it never reach the per-category filter. This is why the model
        runs at the lowest configured floor (never below 0.25) and each category
        then applies its own threshold in :meth:`_effective_min_confidence`.
        """
        base = float(override if override is not None else self.settings.yolo_confidence)
        floors = [
            float(rule.min_confidence)
            for rule in self.rules.values()
            if rule.min_confidence is not None
        ]
        if not floors:
            return base
        return max(0.25, min(base, min(floors)))

    def _effective_min_confidence(self, rule: CategoryRule, override: Optional[float]) -> float:
        base = float(override) if override is not None else self.settings.yolo_confidence
        if rule.min_confidence is not None:
            # An explicit category threshold wins: small objects (cables, phones)
            # can be tuned looser while everything else stays strict.
            return max(0.05, min(1.0, float(rule.min_confidence)))
        return base


class ModelUnavailableError(RuntimeError):
    """Raised when inference is requested but no model/runtime is available."""


_YOLO_SINGLETON: Optional[YoloService] = None
_YOLO_LOCK = threading.Lock()


def get_yolo_service(
    settings: Optional[Settings] = None,
    *,
    rules: Optional[Mapping[str, CategoryRule]] = None,
) -> YoloService:
    global _YOLO_SINGLETON
    with _YOLO_LOCK:
        if _YOLO_SINGLETON is None:
            _YOLO_SINGLETON = YoloService(settings, rules=rules)
        elif rules is not None:
            _YOLO_SINGLETON.rules = dict(rules)
        return _YOLO_SINGLETON


def reset_yolo_service() -> None:
    """Test helper."""
    global _YOLO_SINGLETON
    with _YOLO_LOCK:
        _YOLO_SINGLETON = None
