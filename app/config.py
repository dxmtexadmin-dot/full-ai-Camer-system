"""Application configuration.

All settings come from environment variables (optionally loaded from a local
``.env`` file).  Nothing secret is hard-coded, and the settings object is cheap
to construct so tests can build their own instances.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _path(name: str, default: str) -> Path:
    value = _str(name, default)
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the application configuration."""

    env: str
    host: str
    port: int
    timezone: str
    database_url: str

    secret_key: str
    session_max_age: int
    bootstrap_admin_username: str
    bootstrap_admin_password: str
    bootstrap_admin_fullname: str

    yolo_model_path: Path
    yolo_fallback_model: str
    yolo_confidence: float
    yolo_image_size: int
    yolo_device: str
    yolo_tracker: str
    yolo_dedupe_iou: float

    face_model_name: str
    face_similarity_threshold: float
    face_match_margin: float
    face_providers: tuple[str, ...]
    face_detection_size: tuple[int, int]
    face_recognition_interval: int

    camera_reconnect_interval: int
    camera_max_retry_interval: int
    processing_fps: int
    processing_width: int
    processing_height: int
    max_frame_queue: int
    simulation_mode: bool

    event_cooldown_seconds: int
    evidence_enabled: bool
    evidence_retention_days: int
    evidence_jpeg_quality: int

    # QR inventory: read device labels on the live camera feed. Off by default so
    # the existing camera behaviour is untouched until an operator opts in.
    asset_monitor_enabled: bool
    asset_monitor_interval: int

    google_sheets_enabled: bool
    google_spreadsheet_id: str
    google_service_account_file: Optional[Path]
    google_sync_interval: int
    google_max_retries: int

    log_level: str
    log_dir: Path

    data_dir: Path = field(default=PROJECT_ROOT / "data")
    employees_dir: Path = field(default=PROJECT_ROOT / "data" / "employees")
    evidence_dir: Path = field(default=PROJECT_ROOT / "data" / "evidence")
    models_dir: Path = field(default=PROJECT_ROOT / "data" / "models")
    secrets_dir: Path = field(default=PROJECT_ROOT / "secrets")

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"prod", "production"}

    @property
    def face_detection_size_value(self) -> int:
        """InsightFace expects a single square size."""
        return self.face_detection_size[0]

    def ensure_directories(self) -> None:
        for directory in (
            self.data_dir,
            self.employees_dir,
            self.evidence_dir,
            self.models_dir,
            self.log_dir,
            self.secrets_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def warnings(self) -> list[str]:
        """Configuration problems an operator should fix."""
        problems: list[str] = []
        if self.secret_key.startswith("change-me"):
            problems.append(
                "APP_SECRET_KEY hali o‘zgartirilmagan. Production uchun tasodifiy kalit qo‘ying."
            )
        if self.is_production and self.bootstrap_admin_password == "Admin123!":
            problems.append("Bootstrap administrator paroli default qiymatda qolgan.")
        if not self.yolo_model_path.exists():
            problems.append(
                f"Maxsus YOLO modeli topilmadi: {self.yolo_model_path}. "
                f"Umumiy '{self.yolo_fallback_model}' modeli ishlatiladi - u ofis jihozlarini "
                f"tanimaydi (o‘qitish: docs/yolo_training.md)."
            )
        if self.google_sheets_enabled:
            if not self.google_spreadsheet_id:
                problems.append("GOOGLE_SHEETS_ENABLED=true, ammo GOOGLE_SPREADSHEET_ID bo‘sh.")
            if not self.google_service_account_file or not self.google_service_account_file.exists():
                problems.append("Google service account fayli topilmadi.")
        return problems


def _build_settings() -> Settings:
    data_dir = _path("DATA_DIR", "data")
    providers = tuple(
        p.strip() for p in _str("FACE_PROVIDERS", "CPUExecutionProvider").split(",") if p.strip()
    )
    det_size_raw = _str("FACE_DETECTION_SIZE", "640x640")
    try:
        width, height = (int(part) for part in det_size_raw.lower().split("x"))
    except ValueError:
        width = height = 640

    service_account = _str("GOOGLE_SERVICE_ACCOUNT_FILE", "")
    service_account_path = (
        (Path(service_account) if Path(service_account).is_absolute() else PROJECT_ROOT / service_account)
        if service_account
        else None
    )

    return Settings(
        env=_str("APP_ENV", "development"),
        host=_str("APP_HOST", "127.0.0.1"),
        port=_int("APP_PORT", 8000),
        timezone=_str("APP_TIMEZONE", "Asia/Tashkent"),
        database_url=_str("DATABASE_URL", "sqlite:///./data/inventory.db"),
        secret_key=_str("APP_SECRET_KEY", "dev-only-insecure-secret-key"),
        session_max_age=_int("SESSION_MAX_AGE_SECONDS", 12 * 3600),
        bootstrap_admin_username=_str("BOOTSTRAP_ADMIN_USERNAME", "admin"),
        bootstrap_admin_password=_str("BOOTSTRAP_ADMIN_PASSWORD", "Admin123!"),
        bootstrap_admin_fullname=_str("BOOTSTRAP_ADMIN_FULLNAME", "Tizim administratori"),
        yolo_model_path=_path("YOLO_MODEL_PATH", "data/models/equipment_best.pt"),
        yolo_fallback_model=_str("YOLO_FALLBACK_MODEL", "yolov8s.pt"),
        yolo_confidence=_float("YOLO_CONFIDENCE", 0.35),
        yolo_image_size=_int("YOLO_IMAGE_SIZE", 640),
        yolo_device=_str("YOLO_DEVICE", "cpu"),
        yolo_tracker=_str("YOLO_TRACKER", "bytetrack.yaml"),
        yolo_dedupe_iou=_float("YOLO_DEDUPE_IOU", 0.6),
        face_model_name=_str("FACE_MODEL_NAME", "buffalo_l"),
        face_similarity_threshold=_float("FACE_SIMILARITY_THRESHOLD", 0.45),
        face_match_margin=_float("FACE_MATCH_MARGIN", 0.05),
        face_providers=providers,
        face_detection_size=(width, height),
        face_recognition_interval=max(1, _int("FACE_RECOGNITION_INTERVAL", 10)),
        camera_reconnect_interval=max(1, _int("CAMERA_RECONNECT_INTERVAL", 5)),
        camera_max_retry_interval=max(2, _int("CAMERA_MAX_RETRY_INTERVAL", 60)),
        processing_fps=max(1, _int("PROCESSING_FPS", 10)),
        processing_width=max(160, _int("PROCESSING_WIDTH", 1280)),
        processing_height=max(120, _int("PROCESSING_HEIGHT", 720)),
        max_frame_queue=max(1, _int("MAX_FRAME_QUEUE", 4)),
        simulation_mode=_bool("SIMULATION_MODE", False),
        event_cooldown_seconds=max(1, _int("EVENT_COOLDOWN_SECONDS", 20)),
        evidence_enabled=_bool("EVIDENCE_ENABLED", True),
        evidence_retention_days=max(1, _int("EVIDENCE_RETENTION_DAYS", 30)),
        evidence_jpeg_quality=min(100, max(40, _int("EVIDENCE_JPEG_QUALITY", 85))),
        asset_monitor_enabled=_bool("ASSET_MONITOR_ENABLED", False),
        asset_monitor_interval=max(1, _int("ASSET_MONITOR_INTERVAL", 5)),
        google_sheets_enabled=_bool("GOOGLE_SHEETS_ENABLED", False),
        google_spreadsheet_id=_str("GOOGLE_SPREADSHEET_ID", ""),
        google_service_account_file=service_account_path,
        google_sync_interval=max(15, _int("GOOGLE_SHEETS_SYNC_INTERVAL_SECONDS", 120)),
        google_max_retries=max(1, _int("GOOGLE_SHEETS_MAX_RETRIES", 4)),
        log_level=_str("LOG_LEVEL", "INFO").upper(),
        log_dir=_path("LOG_DIR", "logs"),
        data_dir=data_dir,
        employees_dir=data_dir / "employees",
        evidence_dir=data_dir / "evidence",
        models_dir=data_dir / "models",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings (loaded once)."""
    return _build_settings()


def reload_settings() -> Settings:
    """Reload settings from the environment / .env file."""
    get_settings.cache_clear()
    return get_settings()


def load_env_file() -> None:
    """Load ``.env`` if present (called once at import time)."""
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)


load_env_file()
