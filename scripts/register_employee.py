"""Xodimni ro‘yxatga olish (CLI).

Misollar
--------
    python scripts/register_employee.py --list
    python scripts/register_employee.py --first-name Ali --last-name Valiyev \
        --department "IT bo‘limi" --photos C:\\rasm\\ali1.jpg C:\\rasm\\ali2.jpg
    python scripts/register_employee.py --add-photos EMP001 --photos a.jpg b.jpg

This script uses exactly the same services as the web dashboard, so anything it
creates is visible in the UI immediately.
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
from app.services import employee_service  # noqa: E402
from app.services.face_service import get_face_service  # noqa: E402
from app.utils.logging_config import setup_logging  # noqa: E402


def _read_photos(paths: list[str]) -> list[tuple[str, bytes]]:
    uploads: list[tuple[str, bytes]] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            print(f"  ! Fayl topilmadi: {path}")
            continue
        uploads.append((path.name, path.read_bytes()))
    return uploads


def list_employees() -> int:
    with session_scope() as session:
        employees = employee_service.search(session, active_only=False, limit=1000)
        if not employees:
            print("Bazada xodim yo‘q.")
            return 0
        print(f"{'KOD':<10}{'F.I.SH.':<32}{'BO‘LIM':<22}{'RASM':>5}  HOLAT")
        for employee in employees:
            print(
                f"{employee.employee_code:<10}{employee.full_name[:31]:<32}"
                f"{(employee.department or '—')[:21]:<22}{employee.reference_count:>5}  "
                f"{'faol' if employee.is_active else 'bloklangan'}"
            )
    return 0


def add_photos(code: str, photos: list[str]) -> int:
    uploads = _read_photos(photos)
    if not uploads:
        print("Rasm fayllari berilmadi.")
        return 2
    with session_scope() as session:
        employee = employee_service.get_by_code(session, code)
        if employee is None:
            print(f"Xodim topilmadi: {code}")
            return 2
        stored, errors, warnings = employee_service.add_reference_images(session, employee, uploads)
        print(f"{stored} ta rasm saqlandi, {len(errors)} ta xatolik.")
        for warning in warnings:
            print(f"  ogohlantirish: {warning}")
        for error in errors:
            print(f"  xato: {error}")
        print(f"Jami shablon: {employee.reference_count}")
        return 0 if stored else 1


def register(args: argparse.Namespace) -> int:
    settings = get_settings()
    service = get_face_service(settings)
    if not service.available:
        print(f"OGOHLANTIRISH: yuz modeli yuklanmagan ({service.reason_unavailable}).")
        print("Xodim yaratiladi, lekin biometrik shablon qo‘shilmaydi.")

    with session_scope() as session:
        employee = employee_service.create_employee(
            session,
            first_name=args.first_name,
            last_name=args.last_name,
            department=args.department or "",
            employee_number=args.employee_number,
            employee_code=args.code,
            notes=args.notes,
        )
        print(f"Xodim yaratildi: {employee.employee_code} — {employee.full_name}")
        code = employee.employee_code

    if args.photos:
        return add_photos(code, args.photos)
    print("Rasm yuklanmadi. Keyinroq: --add-photos <kod> --photos ...")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Xodimlarni ro‘yxatga olish")
    parser.add_argument("--list", action="store_true", help="mavjud xodimlarni ko‘rsatish")
    parser.add_argument("--first-name", help="ismi")
    parser.add_argument("--last-name", help="familiyasi")
    parser.add_argument("--department", default="", help="bo‘lim")
    parser.add_argument("--employee-number", default=None, help="tabel raqami")
    parser.add_argument("--code", default=None, help="xodim kodi (bo‘sh bo‘lsa EMP### beriladi)")
    parser.add_argument("--notes", default=None, help="izoh")
    parser.add_argument("--photos", nargs="*", default=[], help="rasm fayllari")
    parser.add_argument("--add-photos", metavar="CODE", help="mavjud xodimga rasm qo‘shish")
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    init_db(settings)

    if args.list:
        return list_employees()
    if args.add_photos:
        return add_photos(args.add_photos, args.photos)
    if not args.first_name or not args.last_name:
        parser.error("--first-name va --last-name majburiy (yoki --list / --add-photos ishlatilsin)")
    return register(args)


if __name__ == "__main__":
    raise SystemExit(main())
