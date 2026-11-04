"""Ochiq YOLO datasetidan (HuggingFace) mos sinflarni loyiha datasetiga ko'chirish.

Muammo: loyihadagi standart `yolov8s.pt` — COCO modeli. U 80 sinfni biladi va
ofis texnikasi haqida juda kam narsa aytadi: `monitor`, `printer`, `speaker`,
`tablet`, `scanner`, `ups` COCO da umuman yo'q, `mouse` esa qo'lda tik
tutilganda `cell phone` deb topiladi. Ishonch chegarasi ham, geometrik filtr ham
buni to'liq tuzata olmaydi — model shu obyektlarni ko'rishi kerak.

Bu skript `hhh134/YOLO-DeskDataset` (80 sinf, 1 646 kadr, YOLO txt) ichidan
loyihaning 27 sinfiga **aniq** mos keladigan kadrlarni tanlab oladi, sinf
raqamlarini loyiha tartibiga o'giradi va `dataset/images|labels/<split>` ga
qo'shadi. Boshqa hech narsaga tegmaydi: `data.yaml` o'zgarmaydi, mavjud kadrlar
ustidan yozilmaydi.

Misollar
--------
    # nima olinishini ko'rish (yuklab olmasdan, faqat label indeksi)
    python scripts/import_public_dataset.py --dry-run

    # har sinfdan 60 kadr (label indeksi avval yuklanadi, keyin faqat tanlangani)
    python scripts/import_public_dataset.py --max-per-class 60

    # faqat sinov to'plami uchun 10 tadan
    python scripts/import_public_dataset.py --split val --max-per-class 10

Muhim
-----
* Ochiq datasetda `person` sinfi **yo'q**. Faqat shu kadrlarda o'qitilgan model
  xodimlarni ko'rmay qoladi, ya'ni yuzni tanish va inventar hodisalari ishlamaydi.
  Shuning uchun kamera kadrlari (`/training` paneli) baribir kerak.
* Kadrda bog'lanmagan obyektlar (krujka, qalam, kitob) label faylidan chiqarib
  tashlanadi — model ularni "fon" deb o'rganadi. Bu kichik shovqin, lekin
  haqiqiy kamera kadrlari bilan aralashtirilsa ta'siri kamayadi.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
for extra in (PROJECT_ROOT, Path(__file__).resolve().parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from train_yolo import CLASS_NAMES, _utf8_console  # noqa: E402

REPO_ID = "hhh134/YOLO-DeskDataset"
API_URL = f"https://huggingface.co/api/datasets/{REPO_ID}"
RAW_URL = f"https://huggingface.co/datasets/{REPO_ID}/resolve/main/"

# Datasetdagi sinf raqami -> loyihadagi toifa kodi.
# Faqat bir xil ma'noni bildiradigan sinflar: `computer` (7) noaniq (tizim bloki
# yoki monitor?), `cable` (17) esa LAN kabel emas — ular ataylab qoldirilgan.
CLASS_MAP: dict[int, str] = {
    8: "laptop",
    9: "mouse",
    10: "keyboard",
    11: "monitor",
    14: "smartphone",
    15: "tablet",
    71: "printer",
    76: "speaker",
}

CLASS_INDEX = {name: index for index, name in enumerate(CLASS_NAMES)}
INDEX_TO_CODE = {index: code for code, index in CLASS_INDEX.items()}


@dataclass(frozen=True)
class Candidate:
    """Bitta manba kadr: label yo'li + unda uchraydigan loyiha sinflari."""

    label_path: str
    split: str
    codes: tuple[str, ...]
    clutter: int  # bog'lanmagan qutilar soni (kamroq = tozaroq label)


def sample_name(source: str, code: str) -> str:
    """Manba nomidan barqaror fayl nomi: `mouse__hfdesk_1a2b3c4d`.

    Nomi nomuvofiq (probel, qavs, kirill) bo'lgani uchun xesh ishlatiladi —
    shunda skript qayta ishga tushirilganda aynan o'sha nom chiqadi va mavjud
    fayllar ustidan yozilmaydi. `__` gacha bo'lgan qism `train_yolo.stem_class`
    uchun sinf belgisi bo'lib xizmat qiladi.
    """
    digest = hashlib.md5(source.encode("utf-8")).hexdigest()[:8]
    return f"{code}__hfdesk_{digest}"


def parse_rows(text: str) -> list[tuple[int, list[str]]]:
    """YOLO label matnini (class_id, [x, y, w, h]) juftliklariga ajratish.

    Normallashtirilgan koordinatalar tekshiriladi: 0..1 dan chiqsa yoki satr
    buzilgan bo'lsa, o'sha quti tashlab yuboriladi (noto'g'ri label o'qitishni
    buzadi).
    """
    rows: list[tuple[int, list[str]]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            class_id = int(float(parts[0]))
            coords = [float(value) for value in parts[1:5]]
        except ValueError:
            continue
        if any(value < 0.0 or value > 1.0 for value in coords):
            continue
        if coords[2] <= 0.0 or coords[3] <= 0.0:
            continue
        rows.append((class_id, [f"{value:.6f}" for value in coords]))
    return rows


def remap_rows(text: str, class_map: dict[int, str] | None = None) -> list[str]:
    """Manba labelini loyiha sinf raqamlari bilan qayta yozish.

    Bog'lanmagan sinflar chiqarib tashlanadi; natija bo'sh bo'lsa kadr kerak
    emas (chaqiruvchi tomonda tekshiriladi).
    """
    mapping = CLASS_MAP if class_map is None else class_map
    lines: list[str] = []
    for class_id, coords in parse_rows(text):
        code = mapping.get(class_id)
        if code is None:
            continue
        index = CLASS_INDEX.get(code)
        if index is None:  # xaritalashda xato — jim o'tmaslik uchun
            raise KeyError(f"`{code}` sinfi loyiha ro'yxatida yo'q")
        lines.append(" ".join([str(index), *coords]))
    return lines


def clutter_count(text: str, class_map: dict[int, str] | None = None) -> int:
    """Kadrda bog'lanmagan (tashlab yuboriladigan) qutilar soni."""
    mapping = CLASS_MAP if class_map is None else class_map
    return sum(1 for class_id, _ in parse_rows(text) if class_id not in mapping)


def plan_selection(
    candidates: list[Candidate], limit: int, classes: set[str] | None = None
) -> list[Candidate]:
    """Har sinf uchun `limit` tagacha kadr tanlash (sinflar bo'yicha navbat).

    Har sinf alohida to'ldiriladi — aks holda `mouse`/`laptop` kabi ko'p
    uchraydigan sinflar kvotani egallab, `tablet`/`speaker` umuman qolmaydi.
    Toza label (kamroq bog'lanmagan quti) ustuvor.
    """
    limit = max(1, int(limit))
    allowed = set(classes) if classes else None
    queues: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        codes = [code for code in candidate.codes if allowed is None or code in allowed]
        if not codes:
            continue
        for code in codes:
            queues.setdefault(code, []).append(candidate)

    for code, items in queues.items():
        items.sort(key=lambda item: (item.clutter, item.label_path))

    taken: dict[str, int] = {code: 0 for code in queues}
    chosen: list[Candidate] = []
    selected: set[str] = set()
    progress = True
    while progress:
        progress = False
        for code in sorted(queues):
            if taken[code] >= limit:
                continue
            queue = queues[code]
            while queue and queue[0].label_path in selected:
                queue.pop(0)
            if not queue:
                continue
            candidate = queue.pop(0)
            selected.add(candidate.label_path)
            chosen.append(candidate)
            taken[code] += 1
            progress = True
    return chosen


def _fetch(url: str, timeout: float, attempts: int = 4) -> bytes:
    """HTTP GET — HuggingFace 429 berganda kutib qayta urinish.

    Bir necha ming kichik faylni parallel so'rash HF tomonidan cheklanadi
    (HTTP 429). Kutish `Retry-After` sarlavhasidan olinadi, bo'lmasa 2, 4, 8
    sekundlik oraliq bilan. Aks holda import yarim yo'lda jimgina to'xtardi.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "face-date/1.0"})
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in {429, 500, 502, 503, 504}:
                raise
            delay = error.headers.get("Retry-After") if error.headers else None
            time.sleep(float(delay) if delay and delay.isdigit() else 2.0 * (attempt + 1))
        except Exception as error:  # noqa: BLE001 - tarmoq uzilishi
            last_error = error
            time.sleep(2.0 * (attempt + 1))
    raise last_error if last_error else RuntimeError(f"yuklab bo'lmadi: {url}")


def _label_cache_path(cache_dir: Path, source: str) -> Path:
    digest = hashlib.md5(source.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.txt"


def load_index(splits: list[str], cache_dir: Path, timeout: float) -> list[Candidate]:
    """Label fayllari ro'yxatini olish va ularni (kesh orqali) yuklash.

    Rasm fayllari emas, faqat label matni yuklanadi — 1 646 ta kadrning
    yorliqlari bir necha yuz kilobayt, shuning uchun 1.2 GB to'plamni to'liq
    yuklash shart emas.
    """
    listing = json.loads(_fetch(API_URL, timeout))
    labels = [
        item["rfilename"]
        for item in listing.get("siblings", [])
        if "/labels/" in item["rfilename"] and item["rfilename"].endswith(".txt")
    ]
    cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_one(source: str) -> tuple[str, str | None]:
        cached = _label_cache_path(cache_dir, source)
        if cached.exists():
            return source, cached.read_text(encoding="utf-8", errors="replace")
        try:
            body = _fetch(RAW_URL + urllib.parse.quote(source), timeout).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - bitta fayl yiqilsa qolgani davom etadi
            return source, None
        cached.write_text(body, encoding="utf-8")
        return source, body

    candidates: list[Candidate] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for source, body in pool.map(fetch_one, labels):
            if body is None:
                continue
            split = next((name for name in splits if f"/{name}/" in source), None)
            if split is None:
                continue
            codes = tuple(
                sorted({CLASS_MAP[class_id] for class_id, _ in parse_rows(body) if class_id in CLASS_MAP})
            )
            if not codes:
                continue
            candidates.append(
                Candidate(
                    label_path=source,
                    split=split,
                    codes=codes,
                    clutter=clutter_count(body),
                )
            )
    return candidates


def import_candidates(
    candidates: list[Candidate],
    dataset_root: Path,
    cache_dir: Path,
    timeout: float,
    dry_run: bool = False,
) -> tuple[dict[str, int], dict[str, int]]:
    """Tanlangan kadrlarni dataset papkasiga nusxalash (label bilan birga).

    Qaytaradi: (fayl nomi prefiksi bo'yicha soni, sinf qamrovi). Ikkinchisi
    muhimroq — bitta kadrda `mouse` va `keyboard` birga bo'lishi mumkin, ya'ni
    yozilgan fayllar soni sinf bo'yicha kadrlar sonidan kichik bo'ladi.
    """
    stats: dict[str, int] = {}
    coverage: dict[str, int] = {}
    for candidate in candidates:
        code = candidate.codes[0]
        stats.setdefault(code, 0)
        stem = sample_name(candidate.label_path, code)
        image_dir = dataset_root / "images" / candidate.split
        label_dir = dataset_root / "labels" / candidate.split
        target_image = image_dir / f"{stem}.jpg"
        target_label = label_dir / f"{stem}.txt"
        if target_image.exists():
            stats[code] += 1
            continue
        if dry_run:
            stats[code] += 1
            for found in candidate.codes:
                coverage[found] = coverage.get(found, 0) + 1
            continue

        cached = _label_cache_path(cache_dir, candidate.label_path)
        body = cached.read_text(encoding="utf-8", errors="replace") if cached.exists() else ""
        lines = remap_rows(body)
        if not lines:
            continue

        image_source = candidate.label_path.replace("/labels/", "/images/").replace(".txt", ".jpg")
        try:
            payload = _fetch(RAW_URL + urllib.parse.quote(image_source), timeout, attempts=6)
        except Exception as error:  # noqa: BLE001
            print(f"  o'tkazib yuborildi ({error}): {image_source}")
            continue
        if payload[:2] != b"\xff\xd8":
            print(f"  JPEG emas, o'tkazib yuborildi: {image_source}")
            continue

        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)
        target_image.write_bytes(payload)
        target_label.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stats[code] += 1
        for found in {INDEX_TO_CODE.get(int(line.split()[0]), "?") for line in lines}:
            coverage[found] = coverage.get(found, 0) + 1
    return stats, coverage


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(
        description="Ochiq YOLO-DeskDataset'dan mos kadrlarni loyiha datasetiga qo'shish"
    )
    parser.add_argument("--dataset", default="dataset", help="loyiha dataset papkasi")
    parser.add_argument(
        "--split",
        default="train",
        choices=("train", "val", "both"),
        help="manba split (loyihadagi images/<split> ga tushadi)",
    )
    parser.add_argument(
        "--max-per-class", type=int, default=60, help="har sinf uchun eng ko'p kadr"
    )
    parser.add_argument(
        "--classes",
        default="",
        help="vergul bilan ajratilgan toifa kodlari (standart: barcha mos sinflar)",
    )
    parser.add_argument(
        "--cache", default="data/.cache/yolo_desk", help="label indeksi keshi"
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP kutish (sekund)")
    parser.add_argument("--dry-run", action="store_true", help="faqat reja, yuklamasdan")
    args = parser.parse_args(argv)

    splits = ["train", "val"] if args.split == "both" else [args.split]
    classes = {item.strip() for item in args.classes.split(",") if item.strip()}
    unknown = classes - set(CLASS_MAP.values())
    if unknown:
        print(f"Noma'lum sinf(lar): {', '.join(sorted(unknown))}")
        print(f"Mavjud: {', '.join(sorted(CLASS_MAP.values()))}")
        return 2

    cache_dir = Path(args.cache)
    started = time.time()
    print(f"Manba: {REPO_ID} (split: {', '.join(splits)})")
    print("Label indeksi yuklanmoqda...")
    candidates = load_index(splits, cache_dir, args.timeout)
    print(f"Mos keladigan kadrlar: {len(candidates)} (indeks {time.time() - started:.1f} s)")

    chosen = plan_selection(candidates, args.max_per_class, classes or None)
    planned: dict[str, int] = {}
    for candidate in chosen:
        for code in candidate.codes:
            planned[code] = planned.get(code, 0) + 1
    print(f"\nTanlandi: {len(chosen)} kadr")
    print("Sinf qamrovi (kadrda uchraydi):")
    for code, count in sorted(planned.items(), key=lambda item: -item[1]):
        print(f"  {code:14s} {count:4d}")
    if not chosen:
        print("\nHech narsa tanlanmadi — --max-per-class yoki --classes ni tekshiring.")
        return 1

    if args.dry_run:
        print("\n--dry-run: hech narsa yuklanmadi va yozilmadi.")
        print("Haqiqiy import: --max-per-class 60 bilan qayta ishga tushiring.")
        return 0

    stats, coverage = import_candidates(chosen, Path(args.dataset), cache_dir, args.timeout)
    total = sum(stats.values())
    print(f"\nDatasetga qo'shildi: {total} kadr")
    for code, count in sorted(coverage.items(), key=lambda item: -item[1]):
        print(f"  {code:14s} {count:4d} kadrda")
    print(f"Manzil: {Path(args.dataset).resolve()}")

    print(
        "\nDiqqat:\n"
        "  1) Bu to'plamda `person` sinfi yo'q — faqat shu kadrlarda o'qitilgan model\n"
        "     xodimlarni ko'rmaydi (yuzni tanish, inventar hodisalari ishlamaydi).\n"
        "     Kamera kadrlarini ham qo'shing: scripts/train_yolo.py --import-samples dataset\n"
        "  2) Kadrdagi bog'lanmagan obyektlar (krujka, qalam, kitob) labeldan olib\n"
        "     tashlandi — model ularni fon deb o'rganadi.\n"
    )
    print("Keyingi qadamlar:")
    print(f"  python scripts/train_yolo.py --check {Path(args.dataset) / 'data.yaml'}")
    print(f"  python scripts/train_yolo.py --split-val 0.2")
    print("  python scripts/train_yolo.py --data dataset/data.yaml --run --epochs 80 --device 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
