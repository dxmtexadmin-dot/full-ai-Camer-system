"""Google Sheets sinxronizatsiyasini qo‘lda ishga tushirish (CLI).

    python scripts/sync_google_sheets.py --status
    python scripts/sync_google_sheets.py

Lokal baza har doim asosiy manba: bu skript faqat hisobot nusxasini yangilaydi.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import init_db, session_scope  # noqa: E402
from app.models import SyncLog  # noqa: E402
from app.services import event_service  # noqa: E402
from app.services.google_sheets_service import get_sheets_service  # noqa: E402
from app.utils.logging_config import setup_logging  # noqa: E402
from app.utils.time_utils import format_local  # noqa: E402


def show_status() -> int:
    settings = get_settings()
    service = get_sheets_service(settings)
    key_path = service.credentials_path
    print("--- Google Sheets holati ---")
    print(f"Yoqilgan         : {settings.google_sheets_enabled}")
    print(f"To‘liq sozlangan : {service.configured}")
    print(f"Spreadsheet ID   : {settings.google_spreadsheet_id or '—'}")
    print(f"Kalit fayli      : {key_path or settings.google_service_account_file or '—'}")
    print(f"Interval         : {settings.google_sync_interval} s")

    if key_path is None:
        print("  ! Service account JSON fayli topilmadi (secrets/ ichiga tashlang).")

    with session_scope() as session:
        pending = event_service.pending_sync_events(session, limit=5000)
        prints = len(pending)
        print(f"Navbatdagi hodisa: {prints}")
        last = session.scalars(select(SyncLog).order_by(SyncLog.started_at.desc()).limit(1)).first()
        if last:
            print(
                f"Oxirgi urinish    : {format_local(last.started_at)} — {last.status}"
                f" (yuborilgan {last.pushed}, xato {last.failed})"
            )
            if last.message:
                print(f"  xabar: {last.message}")
        else:
            print("Oxirgi urinish    : —")
    return 0


def run_sync() -> int:
    settings = get_settings()
    if not settings.google_sheets_enabled:
        print("GOOGLE_SHEETS_ENABLED=false. Avval .env faylida yoqing.")
        print("Keyin servis hisobi JSON faylini GOOGLE_SERVICE_ACCOUNT_FILE ga ko‘rsating.")
        return 2

    service = get_sheets_service(settings)
    with session_scope() as session:
        result = service.sync(session, triggered_by="cli")
        print(f"Holat    : {'OK' if result.ok else 'XATO'}")
        print(f"Natija   : {result.summary}")
        print(f"Yuborildi: {result.pushed}, xato: {result.failed}")
        for sheet, count in result.sheets.items():
            print(f"  {sheet}: {count} qator")
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Google Sheets sinxronizatsiyasi")
    parser.add_argument("--status", action="store_true", help="faqat holatni ko‘rsatish")
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    init_db(settings)

    return show_status() if args.status else run_sync()


if __name__ == "__main__":
    raise SystemExit(main())
