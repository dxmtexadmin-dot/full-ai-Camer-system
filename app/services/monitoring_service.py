"""Runtime supervisor: cameras, pipelines, scheduler, housekeeping.

The FastAPI lifespan owns exactly one ``CameraMonitor``. It is the only place
that starts/stops worker threads, so shutdown is predictable.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.models import Camera, EquipmentCategory, FaceEmbedding
from app.models.enums import CameraStatus, EventType, VerificationStatus
from app.services import evidence_service, event_service
from app.services.camera_service import CameraConfig, CameraWorker
from app.services.face_service import FaceService, get_face_service
from app.services.google_sheets_service import GoogleSheetsService, SheetsScheduler, get_sheets_service
from app.services.pipeline_service import CameraPipeline
from app.services.yolo_service import YoloService, build_rules, get_yolo_service
from app.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

# Galereya boshqa jarayonda (CLI bilan) o‘zgargan bo‘lsa, shu oraliqda
# o‘z-o‘zidan qayta yuklanadi.
GALLERY_WATCH_SECONDS = 20


@dataclass
class CameraRuntime:
    config: CameraConfig
    worker: CameraWorker
    pipeline: Optional[CameraPipeline]
    last_status: CameraStatus = CameraStatus.UNKNOWN


class CameraMonitor:
    """Owns all capture/inference threads."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.face_service: FaceService = get_face_service(self.settings)
        self.yolo_service: YoloService = get_yolo_service(self.settings)
        self.sheets_service: GoogleSheetsService = get_sheets_service(self.settings)
        self.runtimes: dict[str, CameraRuntime] = {}
        self._lock = threading.RLock()
        self._started = False
        self._sheets_scheduler: Optional[SheetsScheduler] = None
        self._janitor: Optional[threading.Thread] = None
        self._janitor_stop = threading.Event()
        self._gallery_watch: Optional[threading.Thread] = None
        self._gallery_stop = threading.Event()
        self.started_at: Optional[datetime] = None

    # -- rules / gallery -------------------------------------------------
    def refresh_detection_rules(self) -> None:
        from app.database import session_scope

        try:
            with session_scope() as session:
                categories = session.scalars(select(EquipmentCategory).where(EquipmentCategory.is_active.is_(True))).all()
                rules = build_rules(categories)
            self.yolo_service.rules = rules
            logger.info("Deteksiya qoidalari yangilandi: %d sinf", len(rules))
        except Exception:  # pragma: no cover - DB dependent
            logger.exception("Deteksiya qoidalarini yangilab bo‘lmadi")

    def refresh_gallery(self) -> int:
        from app.database import session_scope

        try:
            with session_scope() as session:
                return self.face_service.gallery.reload(session)
        except Exception:  # pragma: no cover - DB dependent
            logger.exception("Yuz galereyasini yangilab bo‘lmadi")
            return self.face_service.gallery.size

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self.started_at = utc_now()
        self.refresh_detection_rules()
        self.refresh_gallery()
        self._start_cameras()
        if self.settings.google_sheets_enabled:
            self._sheets_scheduler = SheetsScheduler(self.sheets_service)
            self._sheets_scheduler.start()
        self._start_janitor()
        self._start_gallery_watch()
        logger.info("Monitoring xizmati ishga tushdi")

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
        if self._sheets_scheduler is not None:
            self._sheets_scheduler.stop()
            self._sheets_scheduler = None
        self._janitor_stop.set()
        if self._janitor and self._janitor.is_alive():
            self._janitor.join(timeout=3)
        self._gallery_stop.set()
        if self._gallery_watch and self._gallery_watch.is_alive():
            self._gallery_watch.join(timeout=3)
        self._stop_cameras()
        logger.info("Monitoring xizmati to‘xtatildi")

    @property
    def is_started(self) -> bool:
        return self._started

    def _start_cameras(self) -> None:
        from app.database import session_scope

        with session_scope() as session:
            cameras = session.scalars(select(Camera).where(Camera.is_enabled.is_(True)).order_by(Camera.code)).all()
            configs = [CameraConfig.from_model(camera) for camera in cameras]
        for config in configs:
            self.start_camera(config)
        logger.info("Faol kameralar ishga tushirildi: %d", len(configs))

    def _stop_cameras(self) -> None:
        with self._lock:
            codes = list(self.runtimes)
        for code in codes:
            self.stop_camera(code)

    def start_camera(self, config: CameraConfig) -> None:
        with self._lock:
            existing = self.runtimes.get(config.code)
            if existing is not None and existing.worker.is_running:
                logger.info("Kamera allaqachon ishlayapti: %s", config.code)
                return
            worker = CameraWorker(config, self.settings, on_status_change=self._on_status_change)
            # The pipeline always runs: it draws the live overlay and, when the
            # camera has an entrance/exit line, produces entry/exit events.
            pipeline = CameraPipeline(
                worker,
                config,
                self.settings,
                detector=self.yolo_service,
                face_service=self.face_service,
            )
            self.runtimes[config.code] = CameraRuntime(
                config=config,
                worker=worker,
                pipeline=pipeline,
                last_status=CameraStatus.UNKNOWN,
            )
        worker.start()
        pipeline.start()
        self._set_db_status(config.code, CameraStatus.CONNECTING, "Ishga tushirilmoqda…")

    def stop_camera(self, code: str) -> None:
        with self._lock:
            runtime = self.runtimes.pop(code, None)
        if runtime is None:
            return
        if runtime.pipeline is not None:
            runtime.pipeline.stop()
        runtime.worker.stop()
        self._set_db_status(code, CameraStatus.DISABLED, "To‘xtatildi")

    def reload_all(self) -> None:
        self._stop_cameras()
        self.refresh_detection_rules()
        self.refresh_gallery()
        self._start_cameras()

    # -- status plumbing -------------------------------------------------
    def _on_status_change(self, code: str, status: CameraStatus, message: str) -> None:
        runtime = self.runtimes.get(code)
        previous = runtime.last_status if runtime else CameraStatus.UNKNOWN
        if runtime:
            runtime.last_status = status

        self._set_db_status(code, status, message)

        if status == CameraStatus.OFFLINE and previous != CameraStatus.OFFLINE:
            self._record_camera_event(code, EventType.CAMERA_DISCONNECTED, message)
        elif status == CameraStatus.ONLINE and previous in {CameraStatus.OFFLINE, CameraStatus.ERROR, CameraStatus.CONNECTING}:
            self._record_camera_event(code, EventType.CAMERA_RECONNECTED, message or "Ulanish tiklandi")

    def _set_db_status(self, code: str, status: CameraStatus, message: str) -> None:
        from app.database import session_scope

        try:
            with session_scope() as session:
                camera = session.scalar(select(Camera).where(Camera.code == code))
                if camera is None:
                    return
                camera.status = status.value
                camera.status_message = (message or "")[:400]
                if status == CameraStatus.ONLINE:
                    camera.last_connected_at = utc_now()
                    camera.last_frame_at = utc_now()
                    camera.consecutive_failures = 0
                elif status in {CameraStatus.OFFLINE, CameraStatus.ERROR}:
                    camera.consecutive_failures = (camera.consecutive_failures or 0) + 1
        except Exception:  # pragma: no cover - DB dependent
            logger.exception("Kamera holatini bazaga yozib bo‘lmadi (%s)", code)

    def _record_camera_event(self, code: str, event_type: EventType, message: str) -> None:
        from app.database import session_scope

        try:
            with session_scope() as session:
                event_service.create_event(
                    session,
                    event_type=event_type,
                    camera_code=code,
                    verification_status=VerificationStatus.AUTO,
                    notes=message[:400] or None,
                )
        except Exception:  # pragma: no cover - DB dependent
            logger.exception("Kamera hodisasini yozib bo‘lmadi (%s)", code)

    # -- housekeeping ----------------------------------------------------
    def _start_janitor(self) -> None:
        def loop() -> None:
            while not self._janitor_stop.wait(3600):
                try:
                    evidence_service.purge_expired(self.settings)
                except Exception:  # pragma: no cover
                    logger.exception("Dalillarni tozalash xatosi")

        try:
            evidence_service.purge_expired(self.settings)
        except Exception:  # pragma: no cover
            logger.exception("Dalillarni tozalash xatosi")
        self._janitor = threading.Thread(target=loop, name="evidence-janitor", daemon=True)
        self._janitor.start()

    # -- gallery watch ---------------------------------------------------
    def _gallery_fingerprint(self) -> Optional[tuple[int, int]]:
        """Cheap (count, max_id) snapshot; changes when templates are added."""
        from app.database import session_scope

        try:
            with session_scope() as session:
                count = int(session.scalar(select(func.count(FaceEmbedding.id))) or 0)
                latest = int(session.scalar(select(func.max(FaceEmbedding.id))) or 0)
            return count, latest
        except Exception:  # pragma: no cover - DB dependent
            return None

    def _start_gallery_watch(self) -> None:
        """Reload the gallery when photos were added outside this process.

        Technicians are often enrolled with ``scripts/enroll_employees.py`` while
        the dashboard is running; without this the pipeline would keep matching
        against a stale gallery until somebody pressed “Galereyani yangilash”.
        """

        def loop() -> None:
            fingerprint = self._gallery_fingerprint()
            while not self._gallery_stop.wait(GALLERY_WATCH_SECONDS):
                current = self._gallery_fingerprint()
                if current is None or current == fingerprint:
                    continue
                count = self.refresh_gallery()
                fingerprint = current
                logger.info("Yuz galereyasi o‘zi yangilandi: %d shablon", count)

        self._gallery_watch = threading.Thread(target=loop, name="gallery-watch", daemon=True)
        self._gallery_watch.start()

    # -- queries ---------------------------------------------------------
    def get_runtime(self, code: str) -> Optional[CameraRuntime]:
        with self._lock:
            return self.runtimes.get(code)

    def get_worker(self, code: str) -> Optional[CameraWorker]:
        runtime = self.get_runtime(code)
        return runtime.worker if runtime else None

    def health(self) -> list[dict]:
        with self._lock:
            runtimes = list(self.runtimes.values())
        result = []
        for runtime in runtimes:
            item = runtime.worker.health()
            if runtime.pipeline is not None:
                item.update(runtime.pipeline.health())
            result.append(item)
        return result

    def status_snapshot(self) -> dict:
        return {
            "started": self._started,
            "started_at": self.started_at,
            "cameras": self.health(),
            "yolo": self.yolo_service.status(),
            "face": self.face_service.status(),
            "sheets": self.sheets_service.status(),
        }


_MONITOR: Optional[CameraMonitor] = None
_MONITOR_LOCK = threading.Lock()


def get_monitor(settings: Optional[Settings] = None) -> CameraMonitor:
    global _MONITOR
    with _MONITOR_LOCK:
        if _MONITOR is None:
            _MONITOR = CameraMonitor(settings)
        return _MONITOR


def reset_monitor() -> None:
    """Test helper."""
    global _MONITOR
    with _MONITOR_LOCK:
        _MONITOR = None
