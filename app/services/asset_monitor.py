"""Read device QR labels on the live camera feed (§9.2 of the brief).

The module is deliberately isolated from the detection pipeline: it reuses the
frame the camera worker already produced, decodes labels with OpenCV and stores
the result through ``asset_service``. Nothing here can change an official owner -
every observation is stored as a camera fact and waits for a human decision.

It is opt-in (``ASSET_MONITOR_ENABLED``). While the flag is off the pipeline
behaves exactly as it did before this module existed.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable, Optional

import numpy as np

from app.models.enums import AssetMovementType

logger = logging.getLogger(__name__)

#: The payload is ``https://<host>/qr/<32 hex>``; take only the opaque token.
_TOKEN_RE = re.compile(r"([0-9a-f]{32})", re.IGNORECASE)

#: Consecutive missed observations before a label counts as removed from view.
MISSES_BEFORE_REMOVAL = 2


def token_from_text(text: str) -> str:
    """Extract the label token from a decoded payload ('' when there is none)."""
    match = _TOKEN_RE.search(text or "")
    return match.group(1).lower() if match else ""


class AssetMonitor:
    """Per-camera QR reader. One instance per ``CameraPipeline`` thread."""

    def __init__(
        self,
        camera_code: str,
        session_scope: Callable,
        *,
        interval_seconds: float = 5.0,
        detector=None,
    ) -> None:
        self.camera_code = camera_code or "SYSTEM"
        self.session_scope = session_scope
        self.interval = max(0.5, float(interval_seconds))
        self._detector = detector
        self._last_run = 0.0
        self._misses: dict[str, int] = {}
        self._assets: dict[str, int] = {}
        self.enabled = True

    # -- lifecycle -------------------------------------------------------
    def _ensure_detector(self):
        if self._detector is not None:
            return self._detector
        try:
            import cv2
        except Exception:  # pragma: no cover - OpenCV is a core requirement
            logger.warning("OpenCV topilmadi - QR kuzatuvi o'chirildi")
            self.enabled = False
            return None
        self._detector = cv2.QRCodeDetector()
        return self._detector

    def health(self) -> dict:
        return {
            "camera": self.camera_code,
            "enabled": self.enabled,
            "visible_labels": len(self._assets),
        }

    # -- observation -----------------------------------------------------
    def observe(self, frame: Optional[np.ndarray]) -> dict:
        """Read labels on one frame. Throttled, and never raises to the caller."""
        stats = {"scanned": 0, "unreadable": 0, "removed": 0}
        if not self.enabled or frame is None:
            return stats

        now = time.monotonic()
        if now - self._last_run < self.interval:
            return stats
        self._last_run = now

        detector = self._ensure_detector()
        if detector is None:
            return stats

        try:
            ok, texts, points, _ = detector.detectAndDecodeMulti(frame)
        except Exception:  # pragma: no cover - depends on the OpenCV build
            logger.debug("QR dekodlash xatosi (%s)", self.camera_code, exc_info=True)
            return stats

        tokens = {token_from_text(text) for text in (texts or []) if text}
        tokens.discard("")
        saw_code_shapes = points is not None and len(points) > 0

        try:
            stats = self._persist(tokens, saw_code_shapes)
        except Exception:  # pragma: no cover - DB dependent
            logger.exception("QR kuzatuvini saqlashda xatolik (%s)", self.camera_code)
        return stats

    def _persist(self, tokens: set[str], saw_code_shapes: bool) -> dict:
        from app.services import asset_service

        stats = {"scanned": 0, "unreadable": 0, "removed": 0}
        with self.session_scope() as session:
            for token in tokens:
                asset = asset_service.scan_qr(session, token, camera_code=self.camera_code)
                if asset is None:
                    continue  # unknown or retired label - nothing to attach
                stats["scanned"] += 1
                self._assets[token] = asset.id
                self._misses[token] = 0

            if saw_code_shapes and not tokens:
                # A code-shaped rectangle with no readable payload: the device is
                # present but unidentified, so a human decides (never a guess).
                asset_service.record_unidentified(session, camera_code=self.camera_code)
                stats["unreadable"] = 1

            for token in list(self._assets):
                if token in tokens:
                    continue
                self._misses[token] = self._misses.get(token, 0) + 1
                if self._misses[token] < MISSES_BEFORE_REMOVAL:
                    continue
                asset = asset_service.get_asset(session, self._assets[token])
                if asset is not None:
                    asset_service.observe_movement(
                        session,
                        camera_code=self.camera_code,
                        movement_type=AssetMovementType.TAKEN_OUT,
                        asset=asset,
                        notes="QR belgisi kamera ko'rinishidan chiqdi - tekshirish kerak",
                    )
                    stats["removed"] += 1
                self._assets.pop(token, None)
                self._misses.pop(token, None)
        return stats


def build(camera_code: str, session_scope: Callable, settings) -> Optional[AssetMonitor]:
    """Create the monitor for one camera, or ``None`` when the feature is off."""
    if settings is None or not getattr(settings, "asset_monitor_enabled", False):
        return None
    return AssetMonitor(
        camera_code,
        session_scope,
        interval_seconds=getattr(settings, "asset_monitor_interval", 5),
    )
