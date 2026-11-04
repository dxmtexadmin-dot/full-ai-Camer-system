"""Google Sheets’ga ulanishni bitta buyruq bilan sozlash (CLI).

    python scripts/connect_google_sheets.py --url "https://docs.google.com/spreadsheets/d/<ID>/edit?gid=0#gid=0"
    python scripts/connect_google_sheets.py --url "<havola>" --key "%USERPROFILE%\\Downloads\\kalit.json" --prepare

Bosqichlar: ``secrets/`` papkasini yaratadi, kalitni tekshiradi (kerak bo‘lsa
nusxalaydi), havoladan jadval ID’sini oladi, ``.env`` ni yangilaydi va ulanishni
sinab ko‘radi. Har qanday xato o‘zbekcha izoh bilan chiqadi.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.database import init_db, session_scope  # noqa: E402
from app.services import settings_service  # noqa: E402
from app.services.google_sheets_service import (  # noqa: E402
    CredentialsError,
    GoogleSheetsService,
    parse_spreadsheet_id,
    spreadsheet_id_warning,
)
from app.utils.logging_config import setup_logging  # noqa: E402


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:  # key kept outside the project (e.g. C:\keys\...)
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Google Sheets ulanishini sozlash")
    parser.add_argument("--url", required=True, help="jadval havolasi yoki spreadsheet ID")
    parser.add_argument("--key", help="yuklab olingan service account JSON fayli")
    parser.add_argument("--prepare", action="store_true", help="4 ta varaqni yaratish")
    parser.add_argument("--sync", action="store_true", help="darhol sinxronlash")
    parser.add_argument("--no-enable", action="store_true", help="avtomatik sinxronizatsiyani yoqmaslik")
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir)

    secrets_dir = Path(settings.secrets_dir)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    print(f"Kalit papkasi   : {secrets_dir}")

    try:
        spreadsheet_id = parse_spreadsheet_id(args.url)
    except ValueError as exc:
        print(f"XATO: {exc}")
        return 1
    print(f"Jadval ID       : {spreadsheet_id}")
    if spreadsheet_id_warning(spreadsheet_id):
        print(f"OGOHLANTIRISH   : {spreadsheet_id_warning(spreadsheet_id)}")

    if args.key:
        source = Path(args.key).expanduser()
        if not source.is_file():
            print(f"XATO: kalit fayli topilmadi: {source}")
            return 1
        try:
            stored, info = GoogleSheetsService(settings).save_credentials(
                source.read_text(encoding="utf-8")
            )
        except CredentialsError as exc:
            print(f"XATO: kalit qabul qilinmadi: {exc}")
            return 2
        print(f"Kalit saqlandi  : {stored}")
        print(f"Xizmat manzili  : {info['client_email']}")

    service = GoogleSheetsService(settings)
    key_path = service.credentials_path
    if key_path is None:
        print("XATO: service account JSON topilmadi.")
        print("  1) Google Cloud -> IAM & Admin -> Service Accounts -> Keys -> JSON yuklab oling")
        print(f"  2) faylni {secrets_dir} ichiga tashlang yoki --key \"C:\\...\\kalit.json\" bilan bering")
        print("  3) buyruqni qayta ishga tushiring")
        return 2
    print(f"Kalit fayli     : {key_path}")

    changed = settings_service.update_env(
        {
            "GOOGLE_SPREADSHEET_ID": spreadsheet_id,
            "GOOGLE_SERVICE_ACCOUNT_FILE": _relative(key_path),
            "GOOGLE_SHEETS_ENABLED": "false" if args.no_enable else "true",
        }
    )
    print(f".env yangilandi : {', '.join(changed) or '— (o‘zgarish yo‘q)'}")

    settings = get_settings()
    service = GoogleSheetsService(settings)
    print("Ulanish tekshirilmoqda…")
    result = service.verify()
    if not result["ok"]:
        print(f"XATO: {result['message']}")
        if result.get("hint"):
            print(f"Maslahat: {result['hint']}")
        return 1

    print(f"OK: {result['message']}")
    print(f"Jadval nomi     : {result.get('spreadsheet_title') or '—'}")
    print(f"Varaqlar        : {', '.join(result.get('worksheets') or []) or '—'}")

    if args.prepare:
        outcome = service.prepare_worksheets()
        created = outcome.get("created") or []
        print(f"Varaqlar        : {', '.join(created) if created else 'hammasi mavjud'}")

    if args.sync:
        init_db(settings)
        with session_scope() as session:
            sync = service.sync(session, triggered_by="cli")
        print(f"Sinxronlash     : {'OK' if sync.ok else 'XATO'} — {sync.summary}")

    print("\nEslatma: ishlab turgan server .env ni faqat qayta ishga tushgandan keyin o‘qiydi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
