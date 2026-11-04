"""Xodimlarni (masalan, texniklarni) papkalardan ommaviy ro‘yxatga olish (CLI).

Har xodim uchun alohida papka, ichida 2–5 ta rasm:

    C:\\texniklar\\
        Anvarov Anvar\\   1.jpg  2.jpg  3.jpg
        Karimov Sardor\\  1.jpg  2.jpg

Papka nomi «Familiya Ism» tartibida bo‘ladi (`--given-first` bilan «Ism Familiya»).

Misollar
--------
    python scripts/enroll_employees.py --folder "C:\\texniklar" --dry-run
    python scripts/enroll_employees.py --folder "C:\\texniklar" --department "Texniklar"
    python scripts/enroll_employees.py --check

Skript veb-panel bilan bir xil servislardan foydalanadi. Ishlab turgan server
galereyani o‘zi yangilaydi (20 soniya ichida), qayta ishga tushirish shart emas.
Qayta ishga tushirsangiz nusxa yaratilmaydi: mavjud xodim topilsa, rasmlar
o‘shanga qo‘shiladi (bir xil rasmlar esa tashlab yuboriladi).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from sqlalchemy import func, select

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.database import init_db, session_scope  # noqa: E402
from app.models import Employee, FaceEmbedding  # noqa: E402
from app.services import employee_service  # noqa: E402
from app.services.employee_service import MAX_REFERENCE_PHOTOS  # noqa: E402
from app.utils.logging_config import setup_logging  # noqa: E402

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _photo_files(folder: Path, limit: int) -> list[Path]:
    files = [
        path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    return files[:limit]


def _split_name(folder_name: str, given_first: bool) -> tuple[str, str] | None:
    """«Anvarov Anvar» -> ('Anvar', 'Anvarov')."""
    tokens = [token for token in folder_name.replace("_", " ").replace("-", " ").split() if token]
    if len(tokens) < 2:
        return None
    if given_first:
        return tokens[0], " ".join(tokens[1:])
    return " ".join(tokens[1:]), tokens[0]


def _find_by_name(session, first_name: str, last_name: str) -> Employee | None:
    for employee in session.scalars(select(Employee)).all():
        if (
            employee.first_name.casefold() == first_name.casefold()
            and employee.last_name.casefold() == last_name.casefold()
        ):
            return employee
    return None


def enroll(
    root: Path,
    *,
    department: str,
    max_photos: int,
    given_first: bool,
    dry_run: bool,
) -> int:
    folders = sorted(path for path in root.iterdir() if path.is_dir())
    if not folders:
        print(f"Papkalar topilmadi: {root}")
        return 2

    created = updated = failed = 0
    for folder in folders:
        parsed = _split_name(folder.name, given_first)
        photos = _photo_files(folder, max_photos)
        if parsed is None:
            print(f"  ! {folder.name}: papka nomi «Familiya Ism» ko‘rinishida emas — o‘tkazib yuborildi")
            failed += 1
            continue
        if not photos:
            print(f"  ! {folder.name}: rasm topilmadi (JPG/PNG/WEBP) — o‘tkazib yuborildi")
            failed += 1
            continue

        first_name, last_name = parsed
        if dry_run:
            print(f"  ~ {last_name} {first_name}: {len(photos)} ta rasm -> «{department}» bo‘limiga qo‘shiladi")
            continue

        with session_scope() as session:
            employee = _find_by_name(session, first_name, last_name)
            if employee is None:
                employee = employee_service.create_employee(
                    session,
                    first_name=first_name,
                    last_name=last_name,
                    department=department,
                )
                created += 1
                mark = "+"
            else:
                updated += 1
                mark = "="

            uploads = [(path.name, path.read_bytes()) for path in photos]
            stored, errors, warnings = employee_service.add_reference_images(
                session, employee, uploads
            )
            total = int(
                session.scalar(
                    select(func.count(FaceEmbedding.id)).where(
                        FaceEmbedding.employee_id == employee.id
                    )
                )
                or 0
            )
            print(f"  {mark} {employee.employee_code} {employee.full_name}: {stored} rasm saqlandi, jami {total} shablon")
            for warning in warnings[:3]:
                print(f"      ogohlantirish: {warning}")
            for error in errors[:3]:
                print(f"      xato: {error}")
            if stored == 0 and not errors:
                print("      (barcha rasmlar avval yuklangan)")

    if dry_run:
        print(f"\nSinov rejimi: bazaga hech narsa yozilmadi ({len(folders)} papka tekshirildi).")
        return 0 if failed == 0 else 1

    print(f"\nXodim qo‘shildi: {created}, mavjudiga rasm qo‘shildi: {updated}, xato: {failed}")
    return 0 if failed == 0 else 1


def cross_check(warn_similarity: float) -> int:
    """Two employees with nearly identical templates mean duplicate enrolment."""
    with session_scope() as session:
        names = {employee.id: employee.full_name for employee in session.scalars(select(Employee)).all()}
        vectors: dict[int, list[np.ndarray]] = {}
        for row in session.scalars(select(FaceEmbedding)).all():
            vectors.setdefault(row.employee_id, []).append(row.as_array)

    ids = sorted(vectors)
    total = sum(len(items) for items in vectors.values())
    print(f"Xodimlar: {len(names)}, biometrik shablonlar: {total}")
    for employee_id in ids:
        print(f"  {names.get(employee_id, employee_id)}: {len(vectors[employee_id])} shablon")

    pairs: list[tuple[float, int, int]] = []
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            score = max(float(np.dot(a, b)) for a in vectors[left] for b in vectors[right])
            if score >= warn_similarity:
                pairs.append((score, left, right))
    if not pairs:
        print("\nBarcha xodimlar bir-biridan yetarlicha farq qiladi.")
        return 0

    print(f"\nOGOHLANTIRISH: {warn_similarity:.2f} dan yuqori o‘xshashlik:")
    for score, left, right in sorted(pairs, reverse=True):
        print(f"  {score:.3f}  {names.get(left, left)}  <->  {names.get(right, right)}")
    print("Bu ikki yozuv bitta odam bo‘lishi mumkin — rasmlarni tekshiring, aks holda kamera adashtiradi.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Xodimlarni papkalardan ommaviy ro‘yxatga olish")
    parser.add_argument("--folder", help="ichida har xodim uchun alohida papka bo‘lgan katalog")
    parser.add_argument("--department", default="Texniklar", help="bo‘lim nomi (standart: Texniklar)")
    parser.add_argument(
        "--max-photos",
        type=int,
        default=5,
        help=f"har xodim uchun olinadigan rasm soni (standart 5, maksimal {MAX_REFERENCE_PHOTOS})",
    )
    parser.add_argument("--given-first", action="store_true", help="papka nomi «Ism Familiya» tartibida")
    parser.add_argument("--dry-run", action="store_true", help="faqat ko‘rsatish, bazaga yozmaslik")
    parser.add_argument("--check", action="store_true", help="xodimlar bir-biriga o‘xshamasligini tekshirish")
    parser.add_argument(
        "--warn-similarity",
        type=float,
        default=0.6,
        help="--check uchun ogohlantirish chegarasi (standart 0.6)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    init_db(settings)

    if args.check:
        return cross_check(args.warn_similarity)
    if not args.folder:
        parser.error("--folder yoki --check kerak")

    root = Path(args.folder).expanduser()
    if not root.is_dir():
        print(f"Papka topilmadi: {root}")
        return 2
    limit = max(1, min(args.max_photos, MAX_REFERENCE_PHOTOS))
    code = enroll(
        root,
        department=(args.department or "").strip(),
        max_photos=limit,
        given_first=args.given_first,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        # Hisobot har doim chiqadi, lekin chiqish kodi faqat yozish natijasiga bog‘liq.
        cross_check(args.warn_similarity)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
