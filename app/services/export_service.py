"""Excel (XLSX) and CSV exports for the "Yuklab olish" page.

The dashboard shows tables on screen; this module turns the same read-only data
into files people can actually work with:

* XLSX — frozen header row, auto-filter, sensible column widths, one sheet per
  dataset (or six sheets in ``all.xlsx``);
* CSV — UTF-8 with a BOM, so Excel opens Uzbek text instead of mojibake.

Nothing here writes to the database: exports are read-only projections of the
same service functions the pages already use.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event
from app.models.enums import EventType, TransactionType, VerificationStatus
from app.services import audit_service, employee_service, equipment_service, event_service, inventory_service
from app.utils.time_utils import format_local, local_today

# A single request must never build an unbounded workbook.
MAX_ROWS = 20_000

HEADER_FILL = PatternFill("solid", fgColor="2E5496")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
TITLE_FONT = Font(bold=True, size=13, color="1F3864")
_THIN = Side(style="thin", color="D6DCE5")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


@dataclass(frozen=True)
class Dataset:
    key: str
    title: str
    description: str
    headers: list[str]
    admin_only: bool = False
    date_filtered: bool = False


DATASETS: list[Dataset] = [
    Dataset(
        key="inventory",
        title="Inventar qoldiqlari",
        description="Toifalar bo‘yicha kutilgan qoldiq, kutilayotgan kirim/chiqim va farq.",
        headers=[
            "Toifa kodi",
            "Toifa nomi",
            "Boshlang‘ich",
            "Kutilgan",
            "Kirim kutilmoqda",
            "Chiqim kutilmoqda",
            "Prognoz",
            "Aktivlar",
            "Farq",
            "Muammo",
        ],
    ),
    Dataset(
        key="ledger",
        title="Inventar jurnali",
        description="Append-only qatorlar: har bir amal, miqdor, joylashuv va holat.",
        headers=[
            "Sana",
            "Amal",
            "Toifa",
            "Asset",
            "Miqdor",
            "Joylashuv (dan)",
            "Joylashuv (ga)",
            "Manba",
            "Tasdiqlangan",
            "Rad etilgan",
            "Sabab",
            "Foydalanuvchi",
            "Hodisa",
        ],
        date_filtered=True,
    ),
    Dataset(
        key="events",
        title="Hodisalar (jurnal)",
        description="Kamera hodisalari: kim, qachon, qaysi jihoz, ishonch ballari bilan.",
        headers=[
            "Hodisa ID",
            "Vaqt",
            "Hodisa turi",
            "Holat",
            "Kamera",
            "Xodim kodi",
            "Xodim",
            "Toifa",
            "Asset",
            "Yo‘nalish",
            "Aniqlik",
            "Yuz o‘xshashligi",
            "Bog‘lanish",
            "Dalil",
            "Izoh",
        ],
        date_filtered=True,
    ),
    Dataset(
        key="employees",
        title="Xodimlar",
        description="Xodim kartochkalari, bo‘lim va yuz shablonlari soni.",
        headers=[
            "Kod",
            "Familiya",
            "Ism",
            "F.I.Sh.",
            "Bo‘lim",
            "Tabel raqami",
            "Shablonlar",
            "Holat",
            "Ro‘yxatga olingan",
        ],
    ),
    Dataset(
        key="assets",
        title="Jihozlar (aktivlar)",
        description="Inventar raqamlari, brend/model/seriya va joylashuv.",
        headers=[
            "Asset ID",
            "Toifa kodi",
            "Toifa",
            "Brend",
            "Model",
            "Seriya raqami",
            "Inventar teg",
            "Joylashuv",
            "Holat",
            "Izoh",
            "Ro‘yxatga olingan",
        ],
    ),
    Dataset(
        key="audit",
        title="Audit jurnali",
        description="Kim, qachon, qaysi amalni bajardi (admin uchun).",
        headers=["Vaqt", "Foydalanuvchi", "Amal", "Obyekt", "Obyekt ID", "Sabab", "Tafsilot"],
        admin_only=True,
    ),
]

DATASET_MAP = {item.key: item for item in DATASETS}


def get_dataset(key: str) -> Optional[Dataset]:
    return DATASET_MAP.get(key)


# --------------------------------------------------------------------------- #
#  Row builders (one function per dataset, all read-only)
# --------------------------------------------------------------------------- #
def _event_type_label(code: str) -> str:
    try:
        return EventType(code).label_uz
    except ValueError:
        return code


def _status_label(code: str) -> str:
    try:
        return VerificationStatus(code).label_uz
    except ValueError:
        return code


def _tx_label(code: str) -> str:
    try:
        return TransactionType(code).label_uz
    except ValueError:
        return code


def inventory_rows(session: Session) -> list[list]:
    rows: list[list] = []
    for balance in inventory_service.balances(session):
        rows.append(
            [
                balance.category_code,
                balance.label_uz,
                balance.initial,
                balance.expected,
                balance.pending_in,
                balance.pending_out,
                balance.projected,
                balance.registered_assets,
                balance.discrepancy,
                "bor" if balance.has_issue else "",
            ]
        )
    return rows


def ledger_rows(
    session: Session,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = MAX_ROWS,
) -> list[list]:
    transactions = inventory_service.list_transactions(session, start=start, end=end, limit=limit)
    labels = {category.code: category.label_uz for category in equipment_service.categories(session, include_inactive=True)}

    event_ids = {row.event_id for row in transactions if row.event_id}
    public_ids: dict[int, str] = {}
    if event_ids:
        public_ids = dict(session.execute(select(Event.id, Event.public_id).where(Event.id.in_(event_ids))).all())

    rows: list[list] = []
    for row in transactions:
        rows.append(
            [
                format_local(row.created_at),
                _tx_label(row.transaction_type),
                labels.get(row.category_code, row.category_code),
                row.asset.asset_code if row.asset else "",
                row.quantity,
                row.location_from or "",
                row.location_to or "",
                row.source,
                "ha" if row.is_confirmed else "yo‘q",
                "ha" if row.is_rejected else "yo‘q",
                (row.reason or "").replace("\n", " "),
                row.created_by.username if row.created_by else "",
                public_ids.get(row.event_id, "") if row.event_id else "",
            ]
        )
    return rows


def event_rows(
    session: Session,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = MAX_ROWS,
) -> list[list]:
    events = event_service.query_events(session, limit=limit, start=start, end=end)
    labels = {category.code: category.label_uz for category in equipment_service.categories(session, include_inactive=True)}
    rows: list[list] = []
    for event in events:
        rows.append(
            [
                event.public_id,
                format_local(event.occurred_at),
                _event_type_label(event.event_type),
                _status_label(event.verification_status),
                event.camera_code,
                event.employee.employee_code if event.employee else "",
                event.employee_label,
                labels.get(event.equipment_category, event.equipment_category or ""),
                event.asset.asset_code if event.asset else "",
                event.direction_label_uz,
                round(float(event.detection_confidence), 3) if event.detection_confidence is not None else "",
                round(float(event.face_match_score), 3) if event.face_match_score is not None else "",
                round(float(event.association_confidence), 3) if event.association_confidence is not None else "",
                event.evidence_path or "",
                (event.notes or "").replace("\n", " "),
            ]
        )
    return rows


def employee_rows(session: Session) -> list[list]:
    rows: list[list] = []
    for employee in employee_service.search(session, active_only=False, limit=MAX_ROWS):
        rows.append(
            [
                employee.employee_code,
                employee.last_name,
                employee.first_name,
                employee.full_name,
                employee.department or "",
                employee.employee_number or "",
                employee.reference_count,
                "faol" if employee.is_active else "bloklangan",
                format_local(employee.registered_at),
            ]
        )
    return rows


def asset_rows(session: Session) -> list[list]:
    rows: list[list] = []
    for asset in equipment_service.search_assets(session, limit=MAX_ROWS):
        rows.append(
            [
                asset.asset_code,
                asset.category.code if asset.category else "",
                asset.category.label_uz if asset.category else "",
                asset.brand or "",
                asset.model or "",
                asset.serial_number or "",
                asset.asset_tag or "",
                asset.location or "",
                asset.status_label_uz,
                (asset.notes or "").replace("\n", " "),
                format_local(asset.registered_at),
            ]
        )
    return rows


def audit_rows(session: Session, *, limit: int = MAX_ROWS) -> list[list]:
    rows: list[list] = []
    for entry in audit_service.recent(session, limit=limit):
        details = entry.details or {}
        rows.append(
            [
                format_local(entry.occurred_at),
                entry.username,
                entry.action,
                entry.entity or "",
                entry.entity_id or "",
                entry.reason or "",
                ", ".join(f"{key}={value}" for key, value in details.items())[:300],
            ]
        )
    return rows


def collect(
    session: Session,
    key: str,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> tuple[Dataset, list[list]]:
    """Return the dataset definition and its rows; raises KeyError for unknown keys."""
    dataset = get_dataset(key)
    if dataset is None:
        raise KeyError(key)

    if key == "inventory":
        rows = inventory_rows(session)
    elif key == "ledger":
        rows = ledger_rows(session, start=start, end=end)
    elif key == "events":
        rows = event_rows(session, start=start, end=end)
    elif key == "employees":
        rows = employee_rows(session)
    elif key == "assets":
        rows = asset_rows(session)
    else:
        rows = audit_rows(session)
    return dataset, rows


# --------------------------------------------------------------------------- #
#  Writers
# --------------------------------------------------------------------------- #
def csv_bytes(headers: Sequence[str], rows: Sequence[Sequence]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(list(headers))
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _write_sheet(worksheet, headers: Sequence[str], rows: Sequence[Sequence], *, subtitle: str = "") -> None:
    """Write a header + rows into *worksheet* with filters, widths and formats."""
    header_row = 1
    if subtitle:
        worksheet.append([subtitle])
        worksheet["A1"].font = TITLE_FONT
        worksheet.append([])
        header_row = 3

    worksheet.append(list(headers))
    for cell in worksheet[header_row]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.border = BORDER
        cell.alignment = Alignment(vertical="center", horizontal="left")

    for row in rows:
        worksheet.append(["" if value is None else value for value in row])

    for index, header in enumerate(headers, start=1):
        longest = len(str(header))
        for row in rows[:500]:
            if index <= len(row):
                longest = max(longest, len(str(row[index - 1])))
        worksheet.column_dimensions[get_column_letter(index)].width = min(max(10, longest + 2), 46)

    last_row = header_row + len(rows)
    for row in worksheet.iter_rows(min_row=header_row + 1, max_row=last_row):
        for cell in row:
            cell.border = BORDER
            if isinstance(cell.value, float):
                cell.number_format = "0.000"
            elif isinstance(cell.value, int):
                cell.number_format = "0"

    worksheet.freeze_panes = f"A{header_row + 1}"
    if rows:
        worksheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{last_row}"


def xlsx_bytes(
    headers: Sequence[str],
    rows: Sequence[Sequence],
    *,
    sheet_title: str = "Ma’lumot",
    subtitle: str = "",
) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = (sheet_title or "Ma’lumot")[:31]
    _write_sheet(worksheet, headers, rows, subtitle=subtitle)
    workbook.properties.title = subtitle or sheet_title
    workbook.properties.creator = "AI CCTV inventar tizimi"

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def full_workbook(
    session: Session,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> bytes:
    """One workbook with every dataset as its own sheet (the "hammasi" button)."""
    workbook = Workbook()
    workbook.remove(workbook.active)

    for dataset in DATASETS:
        _, rows = collect(session, dataset.key, start=start, end=end)
        worksheet = workbook.create_sheet(title=dataset.title[:31])
        _write_sheet(worksheet, dataset.headers, rows, subtitle=dataset.title)

    workbook.properties.title = "face-date eksport"
    workbook.properties.creator = "AI CCTV inventar tizimi"
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def filename(key: str, fmt: str) -> str:
    return f"face-date_{key}_{local_today().isoformat()}.{fmt}"


def row_counts(session: Session, *, keys: Optional[Sequence[str]] = None) -> dict[str, int]:
    """Cheap row counters for the download page cards."""
    wanted = set(keys) if keys else None
    counts: dict[str, int] = {}
    for dataset in DATASETS:
        if wanted is not None and dataset.key not in wanted:
            continue
        try:
            _, rows = collect(session, dataset.key, start=None, end=None)
            counts[dataset.key] = len(rows)
        except Exception:  # pragma: no cover - a broken dataset must not kill the page
            counts[dataset.key] = 0
    return counts
