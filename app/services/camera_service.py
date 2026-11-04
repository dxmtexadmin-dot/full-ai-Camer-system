"""Camera capture: RTSP/USB/file with automatic reconnection.

One ``CameraWorker`` thread per camera owns the ``cv2.VideoCapture`` object. It
pushes frames into a *bounded* queue for the inference thread, keeps the last
annotated frame for the MJPEG stream, and reports health transitions to the
monitoring service. Capture and inference are decoupled so a slow model cannot
stall the network read.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from app.config import Settings, get_settings
from app.models.enums import CameraStatus, SourceType
from app.utils.images import encode_jpeg
from app.utils.time_utils import utc_now

logger = logging.getLogger(__name__)


@dataclass
class CameraConfig:
    """Immutable snapshot of the camera row (safe to use from a thread)."""

    id: int
    code: str
    name: str
    location: str
    source_type: str
    source: str
    is_enabled: bool
    line: Optional[tuple[tuple[float, float], tuple[float, float]]] = None
    flip_line: bool = False
    confidence: Optional[float] = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_model(cls, camera) -> "CameraConfig":
        settings = camera.settings or {}
        return cls(
            id=camera.id,
            code=camera.code,
            name=camera.name,
            location=camera.location or "",
            source_type=camera.source_type,
            source=camera.source or "",
            is_enabled=bool(camera.is_enabled),
            line=camera.line,
            flip_line=bool(settings.get("flip_line", False)),
            confidence=camera.confidence_override,
            extra=dict(settings),
        )


# ---------------------------------------------------------------------------
# Frame sources
# ---------------------------------------------------------------------------
class SourceReader:
    """Interface for anything that can deliver frames."""

    def open(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def read(self) -> Optional[np.ndarray]:  # pragma: no cover - interface
        raise NotImplementedError

    def release(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class OpenCVSource(SourceReader):
    """RTSP / USB / video file source."""

    def __init__(self, source: str, source_type: str) -> None:
        self.source_type = source_type
        self.raw = source
        self.capture: Optional[cv2.VideoCapture] = None
        self._source_value: object = source
        if source_type == SourceType.FILE.value:
            raw = source
            if raw.startswith("file:"):
                raw = raw[len("file:") :]
            self._source_value = str(Path(raw))
        elif source_type == SourceType.USB.value:
            digits = source.replace("usb:", "").strip()
            self._source_value = int(digits) if digits.isdigit() else 0

    def open(self) -> bool:
        # Keep only the newest buffered frame; RTSP backlogs are worse than
        # dropped frames. Timeout tuning can be done with
        # OPENCV_FFMPEG_CAPTURE_OPTIONS in the start script.
        try:
            self.capture = cv2.VideoCapture(self._source_value)
        except Exception as exc:  # pragma: no cover - backend dependent
            logger.error("Kamera ochilmadi (%s): %s", self.raw, exc)
            self.capture = None
            return False
        if self.capture is None or not self.capture.isOpened():
            self.release()
            return False
        try:
            self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:  # pragma: no cover - ignored for some backends
            pass
        return True

    def read(self) -> Optional[np.ndarray]:
        if self.capture is None:
            return None
        ok, frame = self.capture.read()
        if not ok or frame is None:
            return None
        return frame

    def release(self) -> None:
        if self.capture is not None:
            try:
                self.capture.release()
            finally:
                self.capture = None


class SimulationSource(SourceReader):
    """Deterministic synthetic scene for offline demos and tests.

    It draws a room, a person walking in/out across the frame and occasionally a
    laptop box. ``ground_truth`` exposes the boxes so the pipeline can run
    end-to-end without a detector - clearly marked as simulation.
    """

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 15, loop_seconds: float = 20.0) -> None:
        self.width = width
        self.height = height
        self.fps = max(1, fps)
        self.loop_seconds = loop_seconds
        self._started = 0.0
        self._stop = False
        self.ground_truth: list[dict] = []

    def open(self) -> bool:
        self._started = time.monotonic()
        return True

    def read(self) -> Optional[np.ndarray]:
        if self._stop:
            return None
        elapsed = (time.monotonic() - self._started) % self.loop_seconds
        half = self.loop_seconds / 2.0
        frame = np.full((self.height, self.width, 3), 32, dtype=np.uint8)

        # floor + door
        cv2.rectangle(frame, (0, int(self.height * 0.55)), (self.width, self.height), (58, 58, 58), -1)
        cv2.rectangle(frame, (int(self.width * 0.82), int(self.height * 0.18)), (self.width, int(self.height * 0.85)), (18, 18, 18), -1)
        cv2.putText(frame, "SIMULYATSIYA", (24, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 255), 2)
        cv2.putText(
            frame,
            f"{self.width}x{self.height} @{self.fps}fps  t={elapsed:05.1f}s",
            (24, 78),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (160, 160, 160),
            1,
        )

        # Person walks left -> right during the first half, right -> left after.
        progress = elapsed / half if elapsed < half else 1.0 - (elapsed - half) / half
        px = int(60 + progress * (self.width - 320))
        person_box = [px, int(self.height * 0.30), px + 150, int(self.height * 0.86)]
        self._draw_person(frame, person_box)

        self.ground_truth = [
            {"bbox": person_box, "category": "person", "label": "person", "confidence": 0.93, "is_person": True, "track_id": 1}
        ]

        # Carry a laptop while walking in the middle third of each pass.
        carrying = 0.30 <= (elapsed % half) / half <= 0.75
        if carrying:
            laptop_box = [px + 20, int(self.height * 0.52), px + 120, int(self.height * 0.66)]
            cv2.rectangle(frame, (laptop_box[0], laptop_box[1]), (laptop_box[2], laptop_box[3]), (200, 200, 40), -1)
            cv2.rectangle(frame, (laptop_box[0], laptop_box[1]), (laptop_box[2], laptop_box[3]), (20, 20, 20), 2)
            cv2.putText(frame, "noutbuk", (laptop_box[0] + 6, laptop_box[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 120), 1)
            self.ground_truth.append(
                {
                    "bbox": laptop_box,
                    "category": "laptop",
                    "label": "laptop",
                    "confidence": 0.81,
                    "is_person": False,
                    "track_id": 2,
                }
            )

        time.sleep(1.0 / self.fps)
        return frame

    def _draw_person(self, frame: np.ndarray, box: list[int]) -> None:
        x1, y1, x2, y2 = box
        cv2.rectangle(frame, (x1, y1), (x2, y2), (90, 150, 230), -1)
        head_r = max(6, (x2 - x1) // 8)
        cv2.circle(frame, ((x1 + x2) // 2, y1 + head_r + 4), head_r, (210, 210, 210), -1)

    def release(self) -> None:
        self._stop = True


def build_source(config: CameraConfig, settings: Settings) -> SourceReader:
    if config.source_type == SourceType.SIMULATION.value or settings.simulation_mode:
        return SimulationSource(width=settings.processing_width, height=settings.processing_height, fps=settings.processing_fps)
    return OpenCVSource(config.source, config.source_type)


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------
class CameraWorker:
    """Owns one capture thread and its health state."""

    def __init__(
        self,
        config: CameraConfig,
        settings: Optional[Settings] = None,
        *,
        on_status_change: Optional[Callable[[str, CameraStatus, str], None]] = None,
    ) -> None:
        self.config = config
        self.settings = settings or get_settings()
        self.on_status_change = on_status_change
        self.frames: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=self.settings.max_frame_queue)
        self.status: CameraStatus = CameraStatus.UNKNOWN
        self.status_message: str = ""
        self.frames_captured = 0
        self.frames_dropped = 0
        self.last_frame_at: Optional[datetime] = None
        self._latest_locked: Optional[np.ndarray] = None
        self._latest_annotated: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._source: Optional[SourceReader] = None
        self._sim_truth: list[dict] = []

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.config.code}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._release_source()
        self._set_status(CameraStatus.DISABLED, "To‘xtatildi")

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # -- status ----------------------------------------------------------
    def _set_status(self, status: CameraStatus, message: str = "") -> None:
        changed = status != self.status or message != self.status_message
        self.status = status
        self.status_message = message
        if changed:
            logger.info("Kamera %s holati: %s (%s)", self.config.code, status.value, message)
        if changed and self.on_status_change:
            try:
                self.on_status_change(self.config.code, status, message)
            except Exception:  # pragma: no cover - callback must never kill the thread
                logger.exception("Kamera holati callback xatosi")

    # -- frames ----------------------------------------------------------
    def latest_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return None if self._latest_locked is None else self._latest_locked.copy()

    def latest_annotated_jpeg(self) -> Optional[bytes]:
        with self._lock:
            frame = self._latest_annotated
            if frame is None:
                frame = self._latest_locked
            if frame is None:
                return None
            try:
                return encode_jpeg(frame, 75)
            except ValueError:
                return None

    def publish_annotated(self, frame: np.ndarray) -> None:
        with self._lock:
            self._latest_annotated = frame

    def simulation_ground_truth(self) -> list[dict]:
        """Boxes from the synthetic scene (empty for real cameras)."""
        with self._lock:
            return list(self._sim_truth)

    def _publish_raw(self, frame: np.ndarray) -> None:
        with self._lock:
            self._latest_locked = frame
            if self._latest_annotated is None:
                self._latest_annotated = frame

    def _push_frame(self, frame: np.ndarray) -> None:
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            # Drop the oldest frame: processing backlog is worse than a skip.
            try:
                self.frames.get_nowait()
                self.frames.put_nowait(frame)
            except queue.Empty:  # pragma: no cover - race
                pass
            self.frames_dropped += 1

    def drain(self) -> Optional[np.ndarray]:
        """Pop the newest frame, discarding older ones (frame skipping)."""
        frame = None
        while True:
            try:
                frame = self.frames.get_nowait()
            except queue.Empty:
                return frame

    # -- capture loop ----------------------------------------------------
    def _release_source(self) -> None:
        if self._source is not None:
            try:
                self._source.release()
            except Exception:  # pragma: no cover
                pass
            self._source = None

    def _open_source(self) -> bool:
        self._release_source()
        self._set_status(CameraStatus.CONNECTING, "Ulanmoqda…")
        source = build_source(self.config, self.settings)
        try:
            opened = source.open()
        except Exception as exc:  # pragma: no cover - backend dependent
            logger.error("Kamera %s ochilmadi: %s", self.config.code, exc)
            opened = False
        if not opened:
            try:
                source.release()
            except Exception:  # pragma: no cover
                pass
            return False
        self._source = source
        return True

    def _run(self) -> None:
        failures = 0
        interval = 1.0 / max(1, self.settings.processing_fps)
        logger.info("Kamera ishga tushdi: %s (%s)", self.config.code, self.config.source_type)

        while not self._stop.is_set():
            if self._source is None:
                if not self._open_source():
                    failures += 1
                    backoff = min(
                        self.settings.camera_max_retry_interval,
                        self.settings.camera_reconnect_interval * (2 ** min(failures - 1, 5)),
                    )
                    self._set_status(
                        CameraStatus.OFFLINE,
                        f"Ulanib bo‘lmadi ({failures}-urinish). {backoff}s dan keyin qayta uriniladi.",
                    )
                    if self._stop.wait(backoff):
                        break
                    continue
                failures = 0
                self._set_status(CameraStatus.ONLINE, "Ulanish o‘rnatildi")

            cycle_started = time.monotonic()
            frame = None
            try:
                frame = self._source.read()
            except Exception as exc:  # pragma: no cover - backend dependent
                logger.warning("Kamera %s o‘qishda xatolik: %s", self.config.code, exc)
                frame = None

            if frame is None:
                failures += 1
                self._release_source()
                if self._stop.is_set():
                    break
                backoff = min(
                    self.settings.camera_max_retry_interval,
                    self.settings.camera_reconnect_interval * (2 ** min(failures - 1, 5)),
                )
                self._set_status(
                    CameraStatus.OFFLINE,
                    f"Kadr olinmadi ({failures}). {backoff}s dan keyin qayta ulanadi.",
                )
                if self._stop.wait(backoff):
                    break
                continue

            frame = self._resize(frame)
            self.frames_captured += 1
            self.last_frame_at = utc_now()
            if isinstance(self._source, SimulationSource):
                with self._lock:
                    self._sim_truth = list(self._source.ground_truth)
            self._publish_raw(frame)
            self._push_frame(frame)

            elapsed = time.monotonic() - cycle_started
            if elapsed < interval and self._stop.wait(interval - elapsed):
                break

        self._release_source()
        logger.info("Kamera to‘xtadi: %s", self.config.code)

    def _resize(self, frame: np.ndarray) -> np.ndarray:
        target_w = self.settings.processing_width
        target_h = self.settings.processing_height
        height, width = frame.shape[:2]
        if width <= target_w and height <= target_h:
            return frame
        scale = min(target_w / width, target_h / height)
        return cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)

    def health(self) -> dict:
        return {
            "code": self.config.code,
            "name": self.config.name,
            "status": self.status.value,
            "message": self.status_message,
            "frames_captured": self.frames_captured,
            "frames_dropped": self.frames_dropped,
            "last_frame_at": self.last_frame_at,
        }
