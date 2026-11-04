"""Google Sheets reporting sync.

Google Sheets is a *reporting mirror*, never the primary store:

* every event is committed to SQLite first;
* this module runs in its own background thread, so API/network problems never
  block camera processing;
* unsynchronised events stay ``PENDING`` and are retried with exponential
  backoff;
* the ``Event ID`` column is used for idempotency, so one event is never
  appended twice even when a previous run failed halfway.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import EquipmentAsset, EquipmentCategory, Employee, Event, SyncLog
from app.models.enums import SyncStatus
from app.services import event_service, inventory_service
from app.utils.time_utils import format_local, utc_now

logger = logging.getLogger(__name__)

EMPLOYEES_SHEET = "Employees"
EQUIPMENT_SHEET = "Equipment"
EVENTS_SHEET = "MovementEvents"
INVENTORY_SHEET = "InventorySummary"

EMPLOYEE_HEADER = ["Employee ID", "First Name", "Last Name", "Department", "Status"]
EQUIPMENT_HEADER = ["Asset ID", "Category", "Brand", "Model", "Serial Number", "Location", "Status"]
EVENT_HEADER = [
    "Event ID",
    "Timestamp",
    "Employee ID",
    "Employee Name",
    "Event Type",
    "Equipment Category",
    "Asset ID",
    "Direction",
    "Camera ID",
    "Verification Status",
    "Notes",
]
INVENTORY_HEADER = [
    "Equipment Category",
    "Expected Quantity",
    "Confirmed Quantity",
    "Pending Movements",
    "Discrepancy",
    "Last Updated",
]

# The four worksheets the mirror always keeps, in creation order.
SHEET_LAYOUT: tuple[tuple[str, list[str]], ...] = (
    (EMPLOYEES_SHEET, EMPLOYEE_HEADER),
    (EQUIPMENT_SHEET, EQUIPMENT_HEADER),
    (EVENTS_SHEET, EVENT_HEADER),
    (INVENTORY_SHEET, INVENTORY_HEADER),
)

SERVICE_ACCOUNT_FILENAME = "google_service_account.json"

_SPREADSHEET_ID_RE = re.compile(r"[A-Za-z0-9_-]{20,}")


class CredentialsError(ValueError):
    """The uploaded service-account JSON cannot be used."""


def parse_spreadsheet_id(value: str) -> str:
    """Accept a full Google Sheets URL or a bare ID and return the ID."""
    text = (value or "").strip().strip('"').strip("'")
    if not text:
        raise ValueError("Jadval havolasi yoki ID kiritilishi kerak.")
    for pattern in (r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", r"[?&]id=([A-Za-z0-9_-]{20,})"):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    if _SPREADSHEET_ID_RE.fullmatch(text):
        return text
    raise ValueError(
        "Havola yoki ID noto‘g‘ri. Namuna: "
        "https://docs.google.com/spreadsheets/d/<ID>/edit"
    )


def spreadsheet_id_warning(value: str) -> str:
    """A soft warning when the ID does not look like a real Sheets ID.

    Real IDs are ~44 characters of base64url text. A short all-digit value is
    usually a Drive/account ID or a truncated copy: Google then answers "not
    found" and the operator cannot tell why, so the UI says it up front.
    """
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) < 30 or text.isdigit():
        return (
            "Jadval ID shubhali ko‘rinadi: Google Sheets havolasidagi ID odatda 44 belgidan "
            "iborat va harflar aralash bo‘ladi. Havolani to‘liq nusxalab qayta kiriting."
        )
    return ""


def discover_service_account_file(directory: Path) -> Optional[Path]:
    """Return the first usable service-account JSON inside ``secrets/``.

    Google downloads keys under a random name (``loyiha-1a2b3c.json``), so the
    operator often just drops the file into ``secrets/``. Instead of demanding
    an exact file name we look for any JSON that really is a service account;
    the configured path always wins when it exists.
    """
    folder = Path(directory)
    if not folder.is_dir():
        return None
    for candidate in sorted(folder.glob("*.json")):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("type") == "service_account" and data.get("client_email"):
            return candidate
    return None


def normalise_credentials(payload: str | dict) -> dict:
    """Validate a service-account JSON payload and return it as a dict."""
    if isinstance(payload, dict):
        data = dict(payload)
    else:
        text = (payload or "").strip()
        if not text:
            raise CredentialsError("JSON fayl bo‘sh.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CredentialsError(f"JSON o‘qilmadi (qator {exc.lineno}): {exc.msg}") from exc
    if not isinstance(data, dict):
        raise CredentialsError("JSON obyekt bo‘lishi kerak, massiv yoki matn emas.")
    if data.get("type") != "service_account":
        raise CredentialsError(
            "Bu service account kaliti emas: 'type' maydoni 'service_account' bo‘lishi kerak."
        )
    email = str(data.get("client_email") or "").strip()
    if "@" not in email or not email.endswith(".iam.gserviceaccount.com"):
        raise CredentialsError("client_email topilmadi yoki service account manziliga o‘xshamaydi.")
    if "BEGIN PRIVATE KEY" not in str(data.get("private_key") or ""):
        raise CredentialsError(
            "private_key maydoni topilmadi — JSON fayl to‘liq saqlanmagan bo‘lishi mumkin."
        )
    data["client_email"] = email
    return data


def connection_hint(exc: Exception) -> str:
    """A short Uzbek explanation for the most common gspread/Google errors."""
    text = str(exc).lower()
    if "not found" in text or "404" in text:
        return "Spreadsheet ID xato yoki jadval o‘chirilgan."
    if "permission" in text or "403" in text or "forbidden" in text:
        return "Jadval service account email’i bilan bo‘lishilmagan (Ruxsat: Muharrir)."
    if "invalid_grant" in text or "private_key" in text or "credential" in text:
        return "Kalit fayli yaroqsiz yoki muddati tugagan — yangi JSON yuklang."
    if "quota" in text or "429" in text:
        return "Google API limiti tugadi — bir necha daqiqadan keyin qayta urinib ko‘ring."
    if any(word in text for word in ("network", "timeout", "getaddrinfo", "ssl", "connection")):
        return "Internet aloqasi yo‘q yoki DNS/maxsus tarmoq to‘sqinligi."
    return ""


def failure_message(exc: Exception, path: Optional[Path] = None) -> tuple[str, str]:
    """Turn a gspread/google-auth exception into (message, hint) for the UI."""
    text = str(exc).lower()
    if isinstance(exc, FileNotFoundError) or "no such file or directory" in text:
        location = str(path) if path else "kalit fayli"
        return (
            f"Service account kalit fayli topilmadi: {location}",
            "2-qadamda kalit JSON faylini qaytadan yuklang — ko‘rsatilgan yo‘lda fayl yo‘q.",
        )
    return f"Ulanib bo‘lmadi: {exc}", connection_hint(exc)


@dataclass
class SyncResult:
    ok: bool
    pushed: int = 0
    failed: int = 0
    message: str = ""
    triggered_by: str = "scheduler"
    sheets: dict[str, int] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        return self.message or f"{self.pushed} yozuv yuborildi, {self.failed} xatolik"


class SheetsUnavailable(RuntimeError):
    pass


class GoogleSheetsService:
    """Thin, testable wrapper around gspread."""

    def __init__(self, settings: Optional[Settings] = None, *, client_factory: Optional[Callable] = None) -> None:
        self.settings = settings or get_settings()
        self._client_factory = client_factory
        self._client = None
        self._lock = threading.Lock()
        self.last_run_at: Optional[datetime] = None
        self.last_result: Optional[SyncResult] = None

    # -- configuration ---------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(
            self.settings.google_sheets_enabled
            and self.settings.google_spreadsheet_id
            and self.settings.google_service_account_file
        )

    @property
    def credentials_path(self) -> Optional[Path]:
        """The key file that will really be used.

        The configured path wins; when it is gone (moved ``.env``, renamed
        download) a service-account JSON dropped into ``secrets/`` is picked up
        automatically so the operator does not have to edit ``.env`` by hand.
        """
        configured = self.settings.google_service_account_file
        if configured and Path(configured).is_file():
            return Path(configured)
        return discover_service_account_file(self.settings.secrets_dir)

    def status(self) -> dict:
        return {
            "enabled": self.settings.google_sheets_enabled,
            "configured": self.configured,
            "spreadsheet_id": self.settings.google_spreadsheet_id,
            "service_account_file": str(self.settings.google_service_account_file or ""),
            "last_run_at": self.last_run_at,
            "last_result": self.last_result.summary if self.last_result else None,
            "interval_seconds": self.settings.google_sync_interval,
            "spreadsheet_id_warning": spreadsheet_id_warning(
                self.settings.google_spreadsheet_id
            ),
        }

    # -- credentials -----------------------------------------------------
    def credentials_info(self) -> dict:
        """Read-only summary of the configured service-account file.

        The private key is never returned: the page only needs the e-mail that
        the spreadsheet has to be shared with.
        """
        path = self.credentials_path or self.settings.google_service_account_file
        configured = self.settings.google_service_account_file
        info = {
            "path": str(path) if path else "",
            "configured_path": str(configured) if configured else "",
            "exists": False,
            "client_email": "",
            "project_id": "",
            "key_id": "",
            "error": "",
        }
        if not path:
            info["error"] = "Kalit fayli sozlanmagan."
            return info
        file_path = Path(path)
        info["exists"] = file_path.exists()
        if not file_path.exists():
            info["error"] = "Fayl topilmadi."
            return info
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            info["error"] = f"Fayl o‘qilmadi: {exc}"
            return info
        if not isinstance(data, dict) or data.get("type") != "service_account":
            info["error"] = "Fayl service account kaliti emas."
            return info
        info["client_email"] = str(data.get("client_email") or "")
        info["project_id"] = str(data.get("project_id") or "")
        info["key_id"] = str(data.get("private_key_id") or "")[:12]
        return info

    def save_credentials(self, payload: str | dict) -> tuple[Path, dict]:
        """Validate and store the JSON key under ``secrets/`` (0600)."""
        data = normalise_credentials(payload)
        directory = Path(self.settings.secrets_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / SERVICE_ACCOUNT_FILENAME
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - Windows ACLs
            logger.debug("Kalit fayli ruxsatlarini o‘zgartirib bo‘lmadi: %s", path)
        tmp.replace(path)
        self.reset_client()
        return path, {
            "client_email": data["client_email"],
            "project_id": str(data.get("project_id") or ""),
        }

    def forget_credentials(self) -> bool:
        """Delete the stored key file (the UI asks for confirmation first)."""
        path = self.credentials_path
        removed = False
        if path:
            file_path = Path(path).resolve()
            try:
                inside_secrets = file_path.parent == Path(self.settings.secrets_dir).resolve()
            except OSError:  # pragma: no cover - broken path
                inside_secrets = False
            if inside_secrets and file_path.is_file():
                file_path.unlink()
                removed = True
        self.reset_client()
        return removed

    def reset_client(self) -> None:
        """Forget the authorised client after settings change."""
        with self._lock:
            self._client = None

    # -- connection check -------------------------------------------------
    def verify(self) -> dict:
        """Open the spreadsheet once and report what is there."""
        if not self.settings.google_sheets_enabled:
            return {"ok": False, "message": "Google Sheets o‘chirilgan — avval yoqing.", "worksheets": []}
        missing_settings = []
        if not self.settings.google_spreadsheet_id:
            missing_settings.append("spreadsheet ID")
        if not self.settings.google_service_account_file:
            missing_settings.append("service account fayli")
        if missing_settings:
            return {
                "ok": False,
                "message": "Sozlamalar to‘liq emas: " + ", ".join(missing_settings) + ".",
                "worksheets": [],
            }
        try:
            self.reset_client()
            book = self.open_spreadsheet()
            titles = [worksheet.title for worksheet in book.worksheets()]
        except Exception as exc:  # noqa: BLE001 - network/API/permission all land here
            logger.warning("Google Sheets sinovi muvaffaqiyatsiz: %s", exc)
            message, hint = failure_message(
                exc, self.credentials_path or self.settings.google_service_account_file
            )
            return {
                "ok": False,
                "message": message,
                "hint": hint,
                "warning": spreadsheet_id_warning(self.settings.google_spreadsheet_id),
                "worksheets": [],
            }
        missing = [title for title, _ in SHEET_LAYOUT if title not in titles]
        message = "Ulanish muvaffaqiyatli."
        if missing:
            message += f" {len(missing)} varaq yo‘q — 'Varaqlarni tayyorlash' bilan qo‘shiladi."
        return {
            "ok": True,
            "message": message,
            "spreadsheet_title": str(getattr(book, "title", "") or ""),
            "worksheets": titles,
            "missing": missing,
            "warning": spreadsheet_id_warning(self.settings.google_spreadsheet_id),
        }

    def prepare_worksheets(self) -> dict:
        """Create the four standard worksheets (with headers) if missing."""
        book = self.open_spreadsheet()
        existing = {worksheet.title for worksheet in book.worksheets()}
        created: list[str] = []
        for title, header in SHEET_LAYOUT:
            if title not in existing:
                created.append(title)
            self._worksheet(book, title, header)
        return {"created": created, "worksheets": [title for title, _ in SHEET_LAYOUT]}

    def _connect(self):
        if self._client is not None:
            return self._client
        if not self.configured:
            raise SheetsUnavailable("Google Sheets sozlanmagan (yoqilmagan yoki kalit yo‘q).")
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except Exception as exc:  # pragma: no cover - dependency dependent
            raise SheetsUnavailable(f"gspread/google-auth import xatosi: {exc}") from exc

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        path = self.credentials_path or self.settings.google_service_account_file
        credentials = Credentials.from_service_account_file(str(path), scopes=scopes)
        self._client = gspread.authorize(credentials)
        return self._client

    def open_spreadsheet(self):
        client = self._connect()
        return client.open_by_key(self.settings.google_spreadsheet_id)

    def _worksheet(self, book, title: str, header: list[str]):
        from gspread.exceptions import WorksheetNotFound

        try:
            worksheet = book.worksheet(title)
        except WorksheetNotFound:
            worksheet = book.add_worksheet(title=title, rows=1000, cols=max(12, len(header)))
        values = worksheet.get_all_values()
        if not values or not values[0]:
            worksheet.update([header], range_name="A1")
        return worksheet

    # -- retry -----------------------------------------------------------
    def _retry(self, operation: Callable, *, attempts: Optional[int] = None, base_delay: float = 1.0):
        attempts = attempts or max(1, self.settings.google_max_retries)
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return operation()
            except Exception as exc:  # noqa: BLE001 - we retry API/network failures
                last_error = exc
                if attempt == attempts:
                    break
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning("Google Sheets urinish %d/%d xato: %s. %.1fs kutish.", attempt, attempts, exc, delay)
                time.sleep(delay)
        raise SheetsUnavailable(str(last_error)) from last_error

    # -- payload builders (pure, easy to test) ----------------------------
    @staticmethod
    def employee_rows(session: Session) -> list[list[str]]:
        employees = session.scalars(select(Employee).order_by(Employee.employee_code)).all()
        return [
            [
                employee.employee_code,
                employee.first_name,
                employee.last_name,
                employee.department or "",
                "Faol" if employee.is_active else "Faolsiz",
            ]
            for employee in employees
        ]

    @staticmethod
    def equipment_rows(session: Session) -> list[list[str]]:
        rows = session.execute(
            select(EquipmentAsset, EquipmentCategory)
            .join(EquipmentCategory, EquipmentAsset.category_id == EquipmentCategory.id)
            .order_by(EquipmentAsset.asset_code)
        ).all()
        return [
            [
                asset.asset_code,
                category.label_uz,
                asset.brand or "",
                asset.model or "",
                asset.serial_number or "",
                asset.location or "",
                asset.status_label_uz,
            ]
            for asset, category in rows
        ]

    @staticmethod
    def event_row(event: Event) -> list[str]:
        return [
            event.public_id,
            format_local(event.occurred_at),
            event.employee.employee_code if event.employee else "",
            event.employee.full_name if event.employee else "",
            event.event_type,
            event.equipment_category or "",
            event.asset.asset_code if event.asset else "",
            event.direction,
            event.camera_code,
            event.verification_status,
            (event.notes or "").replace("\n", " ")[:450],
        ]

    @staticmethod
    def inventory_rows(session: Session) -> list[list[str]]:
        now = format_local(utc_now())
        rows: list[list[str]] = []
        for balance in inventory_service.balances(session):
            if balance.expected == 0 and balance.pending_in == 0 and balance.pending_out == 0 and balance.registered_assets == 0:
                continue
            rows.append(
                [
                    balance.label_uz,
                    str(balance.expected),
                    str(balance.expected),
                    str(balance.pending_in + balance.pending_out),
                    str(balance.discrepancy),
                    now,
                ]
            )
        return rows

    # -- sync ------------------------------------------------------------
    def sync(self, session: Session, *, triggered_by: str = "scheduler") -> SyncResult:
        log = SyncLog(started_at=utc_now(), triggered_by=triggered_by, status=SyncStatus.PENDING.value)
        session.add(log)
        session.flush()

        if not self.settings.google_sheets_enabled:
            result = SyncResult(False, message="Google Sheets o‘chirilgan.", triggered_by=triggered_by)
            self._finish(session, log, result)
            return result
        if not self.configured:
            result = SyncResult(False, message="Google Sheets sozlamalari to‘liq emas.", triggered_by=triggered_by)
            self._finish(session, log, result)
            return result

        try:
            with self._lock:
                book = self.open_spreadsheet()
                result = SyncResult(True, triggered_by=triggered_by)

                employees_job = lambda: self._sync_table(
                    book, EMPLOYEES_SHEET, EMPLOYEE_HEADER, self.employee_rows(session)
                )
                result.sheets[EMPLOYEES_SHEET] = self._retry(employees_job)
                result.pushed += result.sheets[EMPLOYEES_SHEET]

                equipment_job = lambda: self._sync_table(
                    book, EQUIPMENT_SHEET, EQUIPMENT_HEADER, self.equipment_rows(session)
                )
                result.sheets[EQUIPMENT_SHEET] = self._retry(equipment_job)
                result.pushed += result.sheets[EQUIPMENT_SHEET]

                pushed_events, failed_events = self._sync_events(session, book)
                result.sheets[EVENTS_SHEET] = pushed_events
                result.pushed += pushed_events
                result.failed = failed_events

                inventory_job = lambda: self._sync_table(
                    book, INVENTORY_SHEET, INVENTORY_HEADER, self.inventory_rows(session), replace_body=True
                )
                result.sheets[INVENTORY_SHEET] = self._retry(inventory_job)
                result.pushed += result.sheets[INVENTORY_SHEET]

                result.message = "Sinkronizatsiya muvaffaqiyatli"
                self.last_run_at = utc_now()
                self.last_result = result
                self._finish(session, log, result)
                return result
        except FileNotFoundError:
            # A missing key file is a configuration gap, not a crash: record a
            # readable message instead of a traceback on every scheduler tick.
            result = SyncResult(
                False,
                message="Service account kalit fayli topilmadi — Google Sheets’ga ulanilmadi.",
                triggered_by=triggered_by,
            )
            self.last_run_at = utc_now()
            self.last_result = result
            self._finish(session, log, result)
            return result
        except SheetsUnavailable as exc:
            for event in event_service.pending_sync_events(session, limit=200):
                if event.sheets_sync_status == SyncStatus.PENDING.value:
                    event.sheets_sync_status = SyncStatus.FAILED.value
            result = SyncResult(False, message=f"Google Sheets mavjud emas: {exc}", triggered_by=triggered_by)
            self.last_run_at = utc_now()
            self.last_result = result
            self._finish(session, log, result)
            return result
        except Exception as exc:  # pragma: no cover - unexpected API shapes
            logger.exception("Google Sheets sinkronizatsiyasida kutilmagan xatolik")
            result = SyncResult(False, message=f"Xatolik: {exc}", triggered_by=triggered_by)
            self.last_run_at = utc_now()
            self.last_result = result
            self._finish(session, log, result)
            return result

    def _finish(self, session: Session, log: SyncLog, result: SyncResult) -> None:
        log.finished_at = utc_now()
        log.status = SyncStatus.SYNCED.value if result.ok else SyncStatus.FAILED.value
        log.pushed = result.pushed
        log.failed = result.failed
        log.message = result.message
        session.flush()

    # -- table helpers ---------------------------------------------------
    def _sync_table(self, book, title: str, header: list[str], rows: list[list[str]], *, replace_body: bool = False) -> int:
        worksheet = self._worksheet(book, title, header)
        if replace_body:
            existing = worksheet.get_all_values()
            if len(existing) > 1:
                worksheet.batch_clear([f"A2:{chr(64 + len(header))}{len(existing)}"])
            if rows:
                worksheet.update(rows, range_name="A2")
            return len(rows)

        # Upsert by the first column (Employee ID / Asset ID).
        values = worksheet.get_all_values()
        existing: dict[str, tuple[int, list[str]]] = {}
        for index, row in enumerate(values[1:], start=2):
            if row and row[0]:
                existing[row[0]] = (index, row)

        to_append: list[list[str]] = []
        changed = 0
        for row in rows:
            current = existing.get(row[0])
            if current is None:
                to_append.append(row)
                continue
            index, existing_row = current
            padded = (existing_row + [""] * len(header))[: len(header)]
            if padded != row:
                worksheet.update([row], range_name=f"A{index}")
                changed += 1
        if to_append:
            worksheet.append_rows(to_append, value_input_option="USER_ENTERED")
        return len(to_append) + changed

    def _sync_events(self, session: Session, book) -> tuple[int, int]:
        worksheet = self._worksheet(book, EVENTS_SHEET, EVENT_HEADER)
        existing_ids = {row[0] for row in worksheet.get_all_values()[1:] if row}
        pushed = 0
        failed = 0
        for event in event_service.pending_sync_events(session, limit=300):
            if event.public_id in existing_ids:
                event.sheets_sync_status = SyncStatus.SYNCED.value
                continue
            try:
                self._retry(lambda ev=event: worksheet.append_row(self.event_row(ev), value_input_option="USER_ENTERED"))
                event.sheets_sync_status = SyncStatus.SYNCED.value
                existing_ids.add(event.public_id)
                pushed += 1
            except SheetsUnavailable as exc:
                logger.error("Hodisa %s yuborilmadi: %s", event.public_id, exc)
                event.sheets_sync_status = SyncStatus.FAILED.value
                failed += 1
        session.flush()
        return pushed, failed


class SheetsScheduler:
    """Background thread that periodically pushes pending rows."""

    def __init__(self, service: GoogleSheetsService, *, interval_seconds: Optional[int] = None) -> None:
        self.service = service
        self.interval = interval_seconds or service.settings.google_sync_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sheets-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        from app.database import session_scope

        logger.info("Google Sheets scheduler ishga tushdi (har %ss)", self.interval)
        while not self._stop.is_set():
            if self.service.settings.google_sheets_enabled:
                try:
                    with session_scope() as session:
                        self.service.sync(session, triggered_by="scheduler")
                except Exception:  # pragma: no cover - keep the loop alive
                    logger.exception("Rejalashtirilgan sinkronizatsiya xatosi")
            if self._stop.wait(self.interval):
                break


_SERVICE: Optional[GoogleSheetsService] = None
_LOCK = threading.Lock()


def get_sheets_service(settings: Optional[Settings] = None) -> GoogleSheetsService:
    global _SERVICE
    with _LOCK:
        if _SERVICE is None:
            _SERVICE = GoogleSheetsService(settings)
        return _SERVICE


def reset_sheets_service() -> None:
    global _SERVICE
    with _LOCK:
        _SERVICE = None
