"""YOLO modelini o‘qitish uchun yordamchi (CLI).

Bu skript "sehrli" tarzda model yaratmaydi — u dataset tuzilmasini tayyorlaydi,
`data.yaml` ni tekshiradi va Ultralytics buyrug‘ini aynan ko‘rsatib beradi.

Misollar
--------
    python scripts/train_yolo.py --init dataset
    python scripts/train_yolo.py --import-samples dataset
    python scripts/train_yolo.py --split-val 0.2
    python scripts/train_yolo.py --check dataset/data.yaml
    python scripts/train_yolo.py --data dataset/data.yaml --epochs 80
    python scripts/train_yolo.py --data dataset/data.yaml --run --epochs 80 --device 0

`--run` bo‘lmasa hech narsa o‘qitilmaydi, faqat buyruq chop etiladi.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CLASS_NAMES = [
    "person",
    "desktop_pc",
    "monitor",
    "all_in_one_pc",
    "laptop",
    "keyboard",
    "mouse",
    "computer_case",
    "printer",
    "scanner",
    "ups",
    "speaker",
    "network_device",
    "other_equipment",
    "tv",
    "projector",
    "ip_phone",
    "landline_phone",
    "smartphone",
    "tablet",
    "camera",
    "mini_camera",
    "lan_tester",
    "lan_cable",
    "external_hdd",
    "flash_drive",
    "server",
]

# Shundan kam belgilangan kadr bilan o‘qitilgan model prodakshnga avtomatik qo‘yilmaydi.
MIN_DEPLOY_LABELS = 100

# `person` — tizimning tayanchi: yuzni tanish, xodimga bog‘lash va inventar
# hodisalari shu sinfga tayanadi. Datasetda u bo‘lmasa, o‘qitilgan model uni
# umuman ko‘rmaydi va almashtirish mavjud ishlaydigan funksiyani o‘chirib qo‘yadi.
PERSON_CLASS_ID = CLASS_NAMES.index("person")

# Bitta sinf uchun qancha kadr "yetarli" hisoblanadi. Kamroq bo‘lsa `--check`
# jadvalida `kam` deb belgilanadi — bunday sinf modelda deyarli o‘rganilmaydi.
MIN_TRAIN_FRAMES_PER_CLASS = 30

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _utf8_console() -> None:
    """Chiqishni UTF-8 ga o‘tkazish.

    cp866 kod sahifasida `‘` va `—` kabi belgilar yo‘q: avval `print()`
    `UnicodeEncodeError` bilan yiqilardi. Konsol bo‘lsa kod sahifasi ham
    65001 ga o‘tkaziladi, faylga yo‘naltirilganda esa fayl UTF-8 bo‘ladi.
    """
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # pragma: no cover
        except Exception:  # pragma: no cover - konsol bo‘lmasa
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - yo‘naltirilgan oqim
            pass

DATA_YAML = """# AI CCTV inventar tizimi - o'qitish to'plami
# Yo'llar data.yaml fayli joylashgan papkaga nisbatan hisoblanadi.
path: {root}
train: images/train
val: images/val

names:
{names}
"""

READ_ME = """# YOLO o'qitish to'plami

## Papka tuzilmasi

```
{root}/
  data.yaml
  images/train/*.jpg
  images/val/*.jpg
  labels/train/*.txt
  labels/val/*.txt
```

## Talablar

* Kamida 300-500 ta kadr har bir sinf uchun (kamida 50 ta `val` kadr).
* Har bir rasm uchun bir xil nomdagi `.txt` fayl: `class_id cx cy w h`
  (0..1 oralig'ida normallashtirilgan).
* Sinf raqamlari `data.yaml` dagi `names` tartibiga mos bo'lishi shart.
* Kamera kadrlari aynan o'rnatilgan kameradan olinishi kerak (burchak, yorug'lik,
  masofa bir xil bo'lsin).

## Namuna yozuv

```
0 0.512 0.634 0.181 0.402
2 0.702 0.418 0.094 0.128
```

## O'qitish

```
python scripts/train_yolo.py --data {root}/data.yaml --run --epochs 80 --imgsz 640
```

## Tekshirish

```
yolo detect val model=runs/detect/equipment/weights/best.pt data={root}/data.yaml
```

## Modelni ulash

`best.pt` faylini `data/models/equipment_best.pt` ga ko'chiring va
`.env` faylida `YOLO_MODEL_PATH` ni shu faylga qarating, so'ngra
Sozlamalar sahifasida "YOLO modelini qayta yuklash" tugmasini bosing.

## Muhim

* `equipment_best.pt` topilmasa, tizim `YOLO_FALLBACK_MODEL` (yolov8s.pt, COCO)
  bilan ishlaydi. COCO modeli printer, UPS, tizim bloki, IP telefon, LAN tester,
  LAN kabel, mini kamera kabi sinflarni bilmaydi — demo uchun qulay, ishlab
  chiqarish uchun yetarli emas.
* COCO `tv` sinfi `tv` (Televizor) toifasiga, `notebook` sinfi `laptop` ga,
  `cell phone` sinfi `smartphone` ga moslangan (Sozlamalar -> Jihozlar ->
  Toifalar bo'limida tahrirlanadi).
* Sinf nomlari toifa kodlari bilan **bir xil** bo'lishi tavsiya etiladi
  (`ip_phone`, `lan_cable`, ...), shunda model almashtirilganda hech narsa
  o'zgartirish shart bo'lmaydi.
"""


def init_dataset(root: Path) -> int:
    root = root.resolve()
    for relative in ("images/train", "images/val", "labels/train", "labels/val"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    names_block = "\n".join(f"  {index}: {name}" for index, name in enumerate(CLASS_NAMES))
    (root / "data.yaml").write_text(
        DATA_YAML.format(root=root.as_posix(), names=names_block), encoding="utf-8"
    )
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(READ_ME.format(root=root.as_posix()), encoding="utf-8")
    print(f"Dataset tuzilmasi tayyor: {root}")
    print("  images/train, images/val, labels/train, labels/val")
    print(f"  data.yaml ({len(CLASS_NAMES)} sinf)")
    print(f"  README.md — yo‘riqnoma")
    print("\nEndi kadrlarni yuklang va LabelImg / CVAT bilan belgilang.")
    return 0


def count_class_frames(labels_dir: Path, class_count: int = len(CLASS_NAMES)) -> dict[int, int]:
    """Har sinf uchun nechta kadrda kamida bitta quti bor.

    Umumiy rasm soni s infoni ko‘rsatmaydi: 300 kadrning hammasi `mouse` bo‘lib,
    qolgan 20 sinf umuman yo‘q bo‘lishi mumkin. Shu sababli hisob sinf bo‘yicha
    yuritiladi va `--check` jadvalida ko‘rsatiladi.
    """
    counts = {index: 0 for index in range(max(0, class_count))}
    if not labels_dir.is_dir():
        return counts
    for path in labels_dir.glob("*.txt"):
        seen: set[int] = set()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if not parts:
                continue
            try:
                class_id = int(float(parts[0]))
            except ValueError:
                continue
            if class_id in counts:
                seen.add(class_id)
        for class_id in seen:
            counts[class_id] += 1
    return counts


def check_dataset(data_file: Path) -> int:
    if not data_file.is_file():
        print(f"data.yaml topilmadi: {data_file}")
        return 2
    try:
        import yaml
    except ImportError:
        print("PyYAML o‘rnatilmagan. `pip install -r requirements-ai.txt` bajaring.")
        return 2

    payload = yaml.safe_load(data_file.read_text(encoding="utf-8")) or {}
    root = Path(payload.get("path") or data_file.parent)
    if not root.is_absolute():
        root = (data_file.parent / root).resolve()
    names = payload.get("names") or {}
    if isinstance(names, list):
        names = {index: name for index, name in enumerate(names)}

    problems: list[str] = []
    class_counts: dict[str, dict[int, int]] = {}
    print(f"Dataset  : {root}")
    print(f"Sinflar  : {len(names)}")
    if len(names) != len(CLASS_NAMES):
        problems.append(
            f"Sinflar soni {len(names)}, kutilgan {len(CLASS_NAMES)}. "
            "Toifalar jadvali bilan moslashtiring."
        )

    for split_key, folder_key in (("train", "images/train"), ("val", "images/val")):
        rel = payload.get(split_key) or folder_key
        folder = (root / rel) if not Path(rel).is_absolute() else Path(rel)
        images = [p for p in folder.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}]
        labels_dir = Path(str(folder).replace("images", "labels"))
        missing = 0
        for image in images:
            if not (labels_dir / f"{image.stem}.txt").exists():
                missing += 1
        print(f"{split_key:<9}: {len(images)} rasm, {missing} tasdiqsiz")
        if not images:
            problems.append(f"{split_key} bo‘limida rasm yo‘q ({folder}).")
        if missing:
            problems.append(f"{split_key}: {missing} rasm uchun label fayli yo‘q.")
        if len(images) < (50 if split_key == "val" else 100):
            problems.append(f"{split_key} uchun kadrlar juda kam — model o‘rganmaydi.")
        class_counts[split_key] = count_class_frames(labels_dir, len(names))

    print("\nSinf bo‘yicha kadrlar (train / val):")
    empty: list[str] = []
    thin: list[str] = []
    for index in sorted(names):
        name = str(names[index])
        train_frames = class_counts["train"].get(index, 0)
        val_frames = class_counts["val"].get(index, 0)
        marker = ""
        if train_frames == 0:
            empty.append(name)
            marker = "  <- o‘qitish kadri yo‘q"
        elif train_frames < MIN_TRAIN_FRAMES_PER_CLASS:
            thin.append(name)
            marker = f"  <- kam (< {MIN_TRAIN_FRAMES_PER_CLASS})"
        print(f"  {name:<18}{train_frames:5d} /{val_frames:4d}{marker}")
    print(
        f"\nJami {len(names)} sinf: {len(names) - len(empty)} ta o‘qitiladi, "
        f"{len(empty)} ta bo‘sh, {len(thin)} ta kam kadr bilan."
    )

    if problems:
        print("\nMuammolar:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nDataset tekshiruvdan o‘tdi.")
    return 0


def import_samples(dataset_root: Path, samples_root: Path) -> int:
    """Copy the web-collected samples into ``images/train`` for labelling.

    The panel (/training) drops one folder per category under ``data/samples``.
    Names are prefixed with the category code so the labeller immediately sees
    which class the picture belongs to; existing files are never overwritten.
    """
    dataset_root = dataset_root.resolve()
    samples_root = samples_root.resolve()
    if not samples_root.is_dir():
        print(f"Namuna papkasi topilmadi: {samples_root}")
        print("Avval panelda /training bo‘limidan rasmlar yuklang.")
        return 1
    target = dataset_root / "images" / "train"
    target.mkdir(parents=True, exist_ok=True)

    copied = skipped = 0
    per_class: dict[str, int] = {}
    for folder in sorted(path for path in samples_root.iterdir() if path.is_dir()):
        count = 0
        for image in sorted(folder.glob("*.jpg")):
            destination = target / f"{folder.name}__{image.name}"
            if destination.exists():
                skipped += 1
                continue
            destination.write_bytes(image.read_bytes())
            copied += 1
            count += 1
        if count:
            per_class[folder.name] = count

    print(f"Namunalar nusxalandi: {copied} ta ({skipped} ta avvaldan bor edi)")
    print(f"Manzil: {target}")
    for code, count in sorted(per_class.items()):
        print(f"  {code:20s} {count}")
    if copied:
        print("\nEndi shu kadrlarda bounding box chizing (LabelImg/CVAT):")
        print("  har bir kadr uchun labels/train/<nom>.txt — class_id cx cy w h")
        print("  sinf tartibi: python scripts/train_yolo.py --check dataset/data.yaml")
    return 0


def labelled_count(labels_dir: Path) -> int:
    """Nechta kadr belgilangan (har bir `.txt` — bitta kadrning qutilari)."""
    if not labels_dir.is_dir():
        return 0
    return sum(1 for path in labels_dir.glob("*.txt") if path.stat().st_size > 0)


def stem_class(stem: str) -> str:
    """`mouse__mouse_001` -> `mouse`; papka belgisi yo‘q bo‘lsa — `_other`."""
    return stem.split("__", 1)[0] if "__" in stem else "_other"


def plan_val_split(
    stems: list[str], ratio: float = 0.2, labelled: set[str] | None = None
) -> list[str]:
    """`val` uchun qaysi kadrlarni olish kerakligini hisoblash.

    Tanlov sinf bo‘yicha guruhlanadi: har bir sinf `val` da ko‘rinsin, aks holda
    metrika o‘sha sinfni umuman baholamaydi. Belgilangan kadrlar ustuvor —
    belgilanmagan kadr `val` da metrikaga hissa qo‘shmaydi. Guruh oxiridagi
    (odatda eng yangi) kadrlar olinadi — bu vaqt bo‘yicha ajratishga o‘xshaydi.
    Har bir sinfda kamida bitta kadr `train` da qoladi.
    """
    ratio = max(0.05, min(0.5, float(ratio)))
    labelled = labelled or set()
    grouped: dict[str, list[str]] = {}
    for stem in sorted(stems):
        grouped.setdefault(stem_class(stem), []).append(stem)
    chosen: list[str] = []
    for group in grouped.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda stem: (stem in labelled, stem))
        take = min(max(1, round(len(group) * ratio)), len(group) - 1)
        chosen.extend(ordered[-take:])
    return sorted(chosen)


def move_to_val(dataset_root: Path, ratio: float = 0.2, force: bool = False) -> int:
    """`images/train` dan bir qismini `images/val` ga ko‘chirish (label bilan birga)."""
    root = dataset_root.resolve()
    train_images = root / "images" / "train"
    val_images = root / "images" / "val"
    if not train_images.is_dir():
        print(f"Papka topilmadi: {train_images}")
        print("Avval: python scripts/train_yolo.py --init dataset")
        return 2
    existing = (
        [path for path in val_images.glob("*") if path.suffix.lower() in IMAGE_SUFFIXES]
        if val_images.is_dir()
        else []
    )
    if existing and not force:
        # Buyruq takrorlansa, har safar yana 20 % ko‘chib, `val` shishib ketadi.
        print(f"images/val da allaqachon {len(existing)} rasm bor — qayta bo‘linmadi.")
        print("Majburan ko‘chirish uchun --force qo‘shing.")
        return 1
    stems = [path.stem for path in train_images.glob("*") if path.suffix.lower() in IMAGE_SUFFIXES]
    if not stems:
        print(f"{train_images} bo‘sh — avval kadrlarni yuklang.")
        return 1
    labelled_stems = {
        path.stem
        for path in (root / "labels" / "train").glob("*.txt")
        if path.stat().st_size > 0
    }
    chosen = plan_val_split(stems, ratio, labelled_stems)
    if not chosen:
        print("Ko‘chirish uchun yetarli kadr yo‘q (har sinfda kamida 2 ta kerak).")
        return 1

    val_images.mkdir(parents=True, exist_ok=True)
    (root / "labels" / "val").mkdir(parents=True, exist_ok=True)
    moved = 0
    for stem in chosen:
        for image in train_images.glob(f"{stem}.*"):
            if image.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            shutil.move(str(image), str(val_images / image.name))
            moved += 1
            label = root / "labels" / "train" / f"{stem}.txt"
            if label.exists():
                shutil.move(str(label), str(root / "labels" / "val" / label.name))
    print(f"val ga ko‘chirildi: {moved} kadr ({len(chosen)} nom tanlandi)")
    print(f"train: {labelled_count(root / 'labels' / 'train')} belgilangan, "
          f"val: {labelled_count(root / 'labels' / 'val')} belgilangan")
    print("Tekshirish: python scripts/train_yolo.py --check " + str((root / 'data.yaml').as_posix()))
    return 0


def should_deploy(labelled: int, deploy_flag: bool, minimum: int = MIN_DEPLOY_LABELS) -> bool:
    """Zaif dataset bilan o‘qitilgan modelni prodakshnga avtomatik qo‘ymaslik."""
    return deploy_flag or labelled >= minimum


def has_person_labels(labels_dir: Path, class_id: int = PERSON_CLASS_ID) -> bool:
    """Datasetda `person` qutilari bormi.

    Ochiq datasetlardan (masalan YOLO-DeskDataset) yig‘ilgan kadrlarda odam
    yo‘q. Bunday datasetda o‘qitilgan model xodimlarni ko‘rmaydi, ya‘ni yuzni
    tanish va inventarga bog‘lash ishlamay qoladi — shuning uchun almashtirish
    oldidan tekshiriladi.
    """
    if not labels_dir.is_dir():
        return False
    for path in labels_dir.glob("*.txt"):
        try:
            rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:  # pragma: no cover - o‘qish xatosi
            continue
        for row in rows:
            parts = row.split()
            if parts and parts[0].isdigit() and int(parts[0]) == class_id:
                return True
    return False


def build_command(args: argparse.Namespace) -> list[str]:
    return [
        "yolo",
        "detect",
        "train",
        f"model={args.model}",
        f"data={args.data}",
        f"epochs={args.epochs}",
        f"imgsz={args.imgsz}",
        f"batch={args.batch}",
        f"device={args.device}",
        f"project={args.project}",
        f"name={args.name}",
        "patience=25",
        "pretrained=True",
        "plots=True",
    ]


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description="YOLO o‘qitish yordamchisi")
    parser.add_argument("--init", metavar="DIR", help="dataset papka tuzilmasini yaratish")
    parser.add_argument("--check", metavar="DATA_YAML", help="data.yaml ni tekshirish")
    parser.add_argument(
        "--import-samples",
        metavar="DIR",
        help="panelda yig‘ilgan namunalarni dataset DIR/images/train ga nusxalash",
    )
    parser.add_argument(
        "--samples",
        default="data/samples",
        help="namuna rasmlar papkasi (standart: data/samples)",
    )
    parser.add_argument(
        "--split-val",
        type=float,
        metavar="RATIO",
        help="images/train dan RATIO (masalan 0.2) qismini images/val ga ko‘chirish",
    )
    parser.add_argument(
        "--force", help="--split-val ni images/val to‘la bo‘lsa ham bajarish", action="store_true"
    )
    parser.add_argument("--data", default="dataset/data.yaml", help="data.yaml yo‘li")
    parser.add_argument("--model", default="yolov8s.pt", help="boshlang‘ich model")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default="0", help="0 | cpu")
    parser.add_argument("--project", default="runs/detect")
    parser.add_argument("--name", default="equipment")
    parser.add_argument("--run", action="store_true", help="o‘qitishni haqiqatan boshlash")
    parser.add_argument(
        "--deploy",
        action="store_true",
        help=f"kam belgilangan dataset bo‘lsa ham best.pt ni data/models ga ko‘chirish (>= {MIN_DEPLOY_LABELS} ta tavsiya)",
    )
    args = parser.parse_args(argv)

    if args.init:
        return init_dataset(Path(args.init))
    if args.check:
        return check_dataset(Path(args.check))
    if args.import_samples:
        return import_samples(Path(args.import_samples), Path(args.samples))
    if args.split_val is not None:
        dataset_root = Path(args.data).parent if args.data else Path("dataset")
        return move_to_val(dataset_root, args.split_val, args.force)

    command = build_command(args)
    print("Ultralytics buyrug‘i:\n")
    print("  " + " ".join(command))
    print()

    if not args.run:
        print("Hech narsa o‘qitilmadi. Boshlash uchun --run qo‘shing.")
        print("Eslatma: birinchi o‘qitish CPU da uzoq davom etadi; GPU tavsiya etiladi.")
        return 0

    try:
        from ultralytics import YOLO
    except ImportError:
        print("Ultralytics o‘rnatilmagan. `pip install -r requirements-ai.txt` bajaring.")
        return 2

    data_file = Path(args.data)
    if not data_file.is_file():
        print(f"data.yaml topilmadi: {data_file}")
        print("Avval: python scripts/train_yolo.py --init dataset")
        return 2

    print("O‘qitish boshlandi. Jarayon konsolda va log faylida ko‘rinadi.")
    model = YOLO(args.model)
    model.train(
        data=str(data_file),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=args.project,
        name=args.name,
        patience=25,
        pretrained=True,
        plots=True,
    )

    best = Path(args.project) / args.name / "weights" / "best.pt"
    if best.exists():
        target = PROJECT_ROOT / "data" / "models" / "equipment_best.pt"
        labelled = labelled_count(data_file.parent / "labels" / "train")
        if not should_deploy(labelled, args.deploy):
            print(f"\nModel o‘qitildi: {best}")
            print(
                f"Diqqat: datasetda faqat {labelled} ta belgilangan kadr bor "
                f"(kamida {MIN_DEPLOY_LABELS} ta tavsiya etiladi)."
            )
            print("Prodakshn modeli almashtirilmadi — bunday model ko‘p toifani yomonlashtirishi mumkin.")
            print("Baribir almashtirish uchun: --deploy qo‘shing yoki best.pt ni qo‘lda ko‘chiring.")
            return 0
        if not args.deploy and not has_person_labels(data_file.parent / "labels" / "train"):
            # Ochiq datasetlarda `person` yo‘q: deploy qilinsa xodimlarni kuzatish,
            # yuzni tanish va inventar hodisalari ishlamay qoladi.
            print(f"\nModel o‘qitildi: {best}")
            print("Diqqat: datasetda `person` sinfi umuman yo‘q.")
            print(
                "Bu modelni almashtirsak xodimlar ko‘rinmay qoladi — yuzni tanish va "
                "inventar hodisalari ishlamaydi."
            )
            print("Qo‘shimcha kamera kadrlarini belgilang: python scripts/train_yolo.py --import-samples dataset")
            print("Baribir almashtirish uchun: --deploy qo‘shing yoki best.pt ni qo‘lda ko‘chiring.")
            return 0
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, target)
        print(f"\nModel nusxalandi: {target}")
        print("Sozlamalar sahifasida «YOLO modelini qayta yuklash» tugmasini bosing.")
    else:
        print(f"\nbest.pt topilmadi ({best}). Log faylini tekshiring.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
