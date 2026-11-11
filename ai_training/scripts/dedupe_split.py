"""Splitlar orasidagi takroriy kadrlarni topish va karantinga ko'chirish.

Val/test to'plami "ko'rilmagan" bo'lishi shart: bir kadr ham train, ham val
ichida bo'lsa, metrikalar sun'iy ravishda yuqori chiqadi. Skript `--reference`
bo'limini tegilmas holda qoldiradi va `--check` bo'limidagi unga o'xshash
kadrlarni karantinga ko'chiradi (o'chirmaydi).

Miqdor emas, mazmun muhim: karantinga tushgan fayllar `quarantine/<ts>/`
ichida rasmi va label'i bilan saqlanadi, `duplicates.csv` esa nima va nima
bilan mos tushganini yozib qo'yadi.

Misollar
--------
    python ai_training/scripts/dedupe_split.py --dry-run
    python ai_training/scripts/dedupe_split.py --apply
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import sys
import time
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "ai_training" / "dataset"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
NEAR_DUPLICATE_DISTANCE = 3


def image_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def dhash(path: Path, size: int = 8) -> int:
    """64-bit farqlovchi xesh: kichik siljish va siqishga chidamli."""
    with Image.open(path) as image:
        gray = image.convert("L").resize((size + 1, size), Image.LANCZOS)
        pixels = list(gray.getdata())
    bits = 0
    for row in range(size):
        offset = row * (size + 1)
        for column in range(size):
            bits = (bits << 1) | (pixels[offset + column] < pixels[offset + column + 1])
    return bits


def hamming(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


def scan(folder: Path) -> dict[str, tuple[str, int]]:
    """Fayl nomi -> (sha256, dhash)."""
    result: dict[str, tuple[str, int]] = {}
    for path in image_files(folder):
        result[path.name] = (hashlib.sha256(path.read_bytes()).hexdigest(), dhash(path))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Splitlar orasidagi dublikatlarni ajratish")
    parser.add_argument("--dataset", default=str(DATASET))
    parser.add_argument("--reference", default="val", help="tegilmaydigan bo'lim")
    parser.add_argument("--check", default="train", help="tozalanadigan bo'lim")
    parser.add_argument("--intra", default="", help="bo'lim ichidagi dublikatlarni ham tozalash (masalan: val)")
    parser.add_argument("--distance", type=int, default=NEAR_DUPLICATE_DISTANCE,
                        help="dHash Hamming masofasi (shu qiymatdan kichik = dublikat)")
    parser.add_argument("--apply", action="store_true", help="karantinga ko'chirish")
    parser.add_argument("--dry-run", dest="apply", action="store_false")
    parser.set_defaults(apply=False)
    args = parser.parse_args(argv)

    dataset = Path(args.dataset)
    reference_folder = dataset / "images" / args.reference
    check_folder = dataset / "images" / args.check
    reference = scan(reference_folder)
    checked = scan(check_folder)
    if not reference or not checked:
        print(f"Bo'sh bo'lim: {reference_folder} / {check_folder}")
        return 2

    print(f"{args.reference}: {len(reference)} kadr | {args.check}: {len(checked)} kadr")
    print(f"Masofa chegarasi: < {args.distance}\n")

    matches: list[tuple[str, str, str, int]] = []  # (check, reference, tur, masofa)
    for name, (digest, bits) in checked.items():
        for ref_name, (ref_digest, ref_bits) in reference.items():
            if digest == ref_digest:
                matches.append((name, ref_name, "exact", 0))
                break
            distance = hamming(bits, ref_bits)
            if distance < args.distance:
                matches.append((name, ref_name, "near", distance))
                break

    for name, ref_name, kind, distance in matches:
        print(f"  {kind:<5} {name}  <->  {ref_name} (masofa {distance})")
    print(f"\nJami {len(matches)} ta dublikat (aniq: {sum(1 for m in matches if m[2] == 'exact')}).")

    # Bo'lim ichidagi dublikatlar: birinchisi qoladi, keyingilari karantinga tushadi.
    intra: list[tuple[str, str, str, str, int]] = []  # (split, name, kept, tur, masofa)
    for split in [part.strip() for part in args.intra.split(",") if part.strip()]:
        entries = scan(dataset / "images" / split)
        kept: list[tuple[str, str, int]] = []
        for name, (digest, bits) in entries.items():
            twin = None
            for kept_name, kept_digest, kept_bits in kept:
                if digest == kept_digest:
                    twin = (kept_name, "exact", 0)
                    break
                distance = hamming(bits, kept_bits)
                if distance < args.distance:
                    twin = (kept_name, "near", distance)
                    break
            if twin is None:
                kept.append((name, digest, bits))
            else:
                intra.append((split, name, twin[0], twin[1], twin[2]))
        for split_name, name, kept_name, kind, distance in intra:
            if split_name == split:
                print(f"  {kind:<5} {split}/{name}  <->  {split}/{kept_name} (masofa {distance})")

    if not matches and not intra:
        print("\nDublikat yo'q.")
        return 0
    if not args.apply:
        print("\n--dry-run: hech narsa ko'chirilmadi. Ko'chirish uchun: --apply")
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    quarantine = dataset / "quarantine" / stamp
    (quarantine / "images").mkdir(parents=True, exist_ok=True)
    (quarantine / "labels").mkdir(parents=True, exist_ok=True)

    moved = 0
    with (quarantine / "duplicates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file", "duplicate_of", "kind", "distance", "split"])

        def move(split: str, name: str, kept_name: str, kind: str, distance: int) -> None:
            nonlocal moved
            image = dataset / "images" / split / name
            if not image.is_file():
                return
            shutil.move(str(image), str(quarantine / "images" / name))
            label = dataset / "labels" / split / f"{Path(name).stem}.txt"
            if label.is_file():
                shutil.move(str(label), str(quarantine / "labels" / label.name))
            writer.writerow([name, kept_name, kind, distance, split])
            moved += 1

        for name, ref_name, kind, distance in matches:
            move(args.check, name, ref_name, kind, distance)
        for split, name, kept_name, kind, distance in intra:
            move(split, name, kept_name, kind, distance)

    print(f"\n{moved} kadr karantinga ko'chirildi: {quarantine}")
    print("Qaytarish: fayllarni `images/` va `labels/` dan o'z joyiga qayta ko'chiring.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
