# ai_training — custom YOLO model tayyorlash (izolyatsiya qilingan)

Bu papka **faqat model o'qitish** uchun. Asosiy ilova (`app/`) bu papkaga
bog'lanmaydi: hech qanday import, hech qanday runtime yo'li yo'q.

## Nega izolyatsiya

Ishlab turgan tizim — kamera pipeline'i, inventar ledger'i va Google Sheets
sinxronizatsiyasi — o'qitish tajribalari tufayli to'xtamasligi kerak. Shuning
uchun:

* o'qitish **alohida virtual muhitda** (`ai_training/.venv`, CUDA torch) bajariladi;
* asosiy `.venv` (production, torch **CPU**) tegmaydi;
* model faqat `ai_training/models/` va `ai_training/runs/` da tug'iladi;
* `data/models/equipment_candidate.pt` — yagona "ko'chirish nuqtasi";
  `data/models/equipment_best.pt` (production) va `data/models/yolov8s.pt`
  (fallback) **hech qachon ustiga yozilmaydi**;
* `.env` faqat `export.py --switch` bilan va avval `.env.bak-<vaqt>` nusxasi olinib o'zgartiriladi.

## Muhitni tayyorlash (bir marta)

```powershell
py -3.12 -m venv ai_training\.venv
ai_training\.venv\Scripts\python -m pip install --upgrade pip setuptools wheel
ai_training\.venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
ai_training\.venv\Scripts\python -m pip install ultralytics==8.4.199 lapx pyyaml
```

Tekshirish (CUDA haqiqatan ishlayaptimi):

```powershell
ai_training\.venv\Scripts\python -c "import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

`False` chiqsa — GPU o'qitish ishlamaydi, xato yashirilmaydi: `train.py` ham
shu holatda ishga tushmaydi va aniq yo'l-yo'riq beradi.

## Papka tuzilmasi

```
ai_training/
  configs/training.yaml      # giperparametrlar + qabul chegaralari
  dataset/
    data.yaml                # 27 sinf (scripts/train_yolo.py --init bilan yaratilgan)
    raw/<sessiya>/           # kameradan yig'ilgan xom kadrlar (gitignore)
    images/{train,val,test}  # o'qitish to'plami (gitignore)
    labels/{train,val,test}  # YOLO label'lari (gitignore)
    manifests/*.json         # dataset versiyasi: sha256 + hisoblar
  models/<run>/best.pt       # checkpoint arxivi (gitignore)
  runs/<run>/                # ultralytics natijalari: loglar, grafiklar, results.csv
  reports/                   # baholash, benchmark, label ko'rigi, model kartasi
  scripts/                   # quyidagi 9 skript
    collect_frames.py        # kameradan kadr yig'ish (ilova API'si orqali)
    prepare_dataset.py       # raw -> images/labels, manifest
    dedupe_split.py          # splitlar orasidagi/ichidagi dublikatlarni karantinga
    validate_labels.py       # label QA + label_review.csv
    train.py                 # o'qitish, resume, checkpoint arxivi
    evaluate.py              # val/test metrikasi + qabul chegaralari
    benchmark.py             # eski/yangi modelni bir xil kadrlarda taqqoslash
    export.py                # candidate saqlash, --switch / --rollback
    integration_check.py     # model <-> ilova kodi (27 sinf, detect, ByteTrack)
```

## Ish oqimi (tartib bilan)

### 1. Kadr yig'ish — kamera band bo'lsa ham xavfsiz

Kadrlar **ilovaning o'z API'sidan** olinadi (`/api/cameras/<kod>/snapshot`), shuning
uchun USB kamera `CameraWorker` bilan to'qnashmaydi va kadr aynan production
ko'rinishida bo'ladi.

```powershell
.venv\Scripts\python ai_training\scripts\collect_frames.py --camera 101 --count 140 --interval 1.2
```

Natija: `dataset/raw/session_YYYYmmdd_HHMMSS/` + `session.json` (metadata).

**Ma'lum cheklov:** `/snapshot` endpoint **chizilgan** kadr qaytaradi
(`latest_annotated_jpeg()`) — ustida kamera nomi, chegara va virtual chiziq bor.
Bu dataset uchun ideal emas: chiziq/banner o'qitishda "obyekt" bo'lib
qolmasligi uchun label'lanmaydi, keyinchalik `benchmark.py` da yolg'on
detektsiya kuzatiladi. Kelajakda pipeline ichidan xom kadr olish uchun
`CameraWorker.latest_frame()` dan foydalanadigan eksport nuqtasi qo'shilishi
mumkin — bu asosiy kodga tegish bo'lgani uchun hozircha qilinmadi.

### 2. Datasetni yig'ish va bo'lish

```powershell
.venv\Scripts\python ai_training\scripts\prepare_dataset.py --all --ratio 0.2
```

* aynan bir xil (sha256) va deyarli bir xil (dHash) kadrlar tashlanadi —
  `train`/`val` orasida takrorlanish bo'lmaydi;
* bo'linish loyihaning o'z skripti bilan: `scripts/train_yolo.py --split-val`
  (vaqt bo'yicha: `val` — eng yangi kadrlar);
* `dataset/manifests/dataset_<vaqt>.json` — versiya nazorati (har bir fayl sha256 bilan).
  Faqat manifestni yangilash: `prepare_dataset.py --manifest-only`.

Splitlar orasida yoki ichida takror kadr qolgan bo'lsa (masalan, ochiq datasetning
_o'z_ train/val bo'linmasidan), sizib chiqish (leakage) paydo bo'ladi va val
metrikasi sun'iy yuqori chiqadi. Tekshirish va karantinga ajratish:

```powershell
# ko'rish (hech narsa ko'chirilmaydi)
.venv\Scripts\python ai_training\scripts\dedupe_split.py --check train --reference val
.venv\Scripts\python ai_training\scripts\dedupe_split.py --intra val,train

# karantinga ko'chirish (o'chirilmaydi) + duplicates.csv
.venv\Scripts\python ai_training\scripts\dedupe_split.py --check train --reference val --apply
```

### 3. Label'lar (avtomatik taklif + inson ko'rigi)

```powershell
.venv\Scripts\python scripts\autolabel_dataset.py --dataset ai_training\dataset --split train
ai_training\.venv\Scripts\python ai_training\scripts\validate_labels.py
```

* `autolabel_dataset.py` — COCO modelidan **taklif**; u 100% to'g'ri deb qabul qilinmaydi.
* `validate_labels.py` xatolarni (format, koordinata, sinf raqami, takror kadr)
  va shubhali qutilarni (juda kichik, chetga chiqqan, butun kadr) ajratadi.
* `reports/label_review.csv` — har bir quti uchun qator. Inson shu jadvalni
  ko'rib, `.txt` fayllarni tuzatadi (LabelImg / CVAT ham mumkin).
* Xato bo'lsa skript `1` bilan chiqadi — bunday dataset bilan o'qitish boshlanmaydi.

### 4. O'qitish

```powershell
ai_training\.venv\Scripts\python ai_training\scripts\train.py --dry-run   # parametrlarni ko'rish
ai_training\.venv\Scripts\python ai_training\scripts\train.py              # o'qitish
ai_training\.venv\Scripts\python ai_training\scripts\train.py --resume ai_training/runs/<run>  # davom ettirish
```

* pretrained `yolo26m.pt` avtomatik `ai_training/models/` ga yuklanadi;
* `best.pt` **va** `last.pt` saqlanadi (+ `ai_training/models/<run>/` arxivi);
* `data/models/` ga **hech narsa ko'chirilmaydi** — deploy alohida qadam.

### 5. Baholash va taqqoslash

```powershell
ai_training\.venv\Scripts\python ai_training\scripts\evaluate.py --weights ai_training\models\<run>\best.pt
ai_training\.venv\Scripts\python ai_training\scripts\benchmark.py --weights ai_training\models\<run>\best.pt
```

* `evaluate.py` — mAP50 / mAP50-95 / sinf bo'yicha mAP **va recall** +
  `configs/training.yaml` dagi qabul chegaralari (`min_mAP50`, `min_person_recall`);
* sinf bo'yicha metrika `ap_class_index` orqali olinadi: label'i yo'q sinflar
  ultralytics `maps` massivida o'rtacha qiymat bilan to'ldiriladi, shuning uchun
  ularni "0.68 mAP" deb o'qish xato bo'lardi;
* `benchmark.py` — production model bilan **bir xil kadrlarda** taqqoslash:
  CPU kechikish (production CPU da ishlaydi), aniqlangan obyektlar, `person` recall.

### 5.1 Ilova kodi bilan moslik

```powershell
.venv\Scripts\python ai_training\scripts\integration_check.py --weights data\models\equipment_candidate.pt
```

Model `YoloService` ga **production qoidalari bilan** (DB'dagi faol 27 kategoriya →
`build_rules`) yuklanadi va `detect()` / ByteTrack sinaladi. Bu — kod o'zgartirmasdan
almashtirish mumkinligini isbotlaydi (yoki isbotlamaydi).

### 6. Candidate sifatida saqlash va (kerak bo'lsa) yoqish

```powershell
# faqat saqlash — production o'zgarmaydi
ai_training\.venv\Scripts\python ai_training\scripts\export.py --weights ai_training\models\<run>\best.pt

# .env dagi YOLO_MODEL_PATH ni candidate ga o'tkazish (nusxa olinadi)
ai_training\.venv\Scripts\python ai_training\scripts\export.py --switch --force

# qaytarish
ai_training\.venv\Scripts\python ai_training\scripts\export.py --rollback
```

Yoqilgandan keyin: `.venv\Scripts\python run.py` (yoki Sozlamalar sahifasida
«YOLO modelini qayta yuklash»). Model yuklanmasa, ilova o'zi `YOLO_FALLBACK_MODEL`
ga qaytadi — tizim ko'r bo'lib qolmaydi.

## Qat'iy qoidalar

1. `data/models/equipment_best.pt` va `yolov8s.pt` ustiga yozish taqiqlanadi
   (`export.py` buni rad etadi).
2. `dataset/` da `person` sinfi bo'lmasa yoki belgilangan kadr < 100 bo'lsa —
   model productionga chiqarilmaydi.
3. O'qitish asosiy `.venv` da emas, `ai_training/.venv` da bajariladi.
4. Inson ko'rigidan o'tmagan label'lar bilan olingan natija "tajriba" deb
   hisoblanadi, "tayyor model" deb emas.
5. Har bir o'zgarishdan keyin regression: `pytest -q` (asosiy `.venv` da).

## Muammolar

| Belgi | Sabab | Yechim |
|---|---|---|
| `CUDA out of memory` | batch/imgsz katta | `--batch 8` yoki `--imgsz 512` |
| `CUDA topilmadi` | CPU build o'rnatilgan | CUDA indeksidan qayta o'rnatish (yuqorida) |
| `data.yaml topilmadi` | dataset yaratilmagan | `scripts\train_yolo.py --init ai_training\dataset` |
| `kadr olinmadi (HTTP 503)` | kamera OFFLINE | `/cameras` sahifasida holatni tekshirish |
| Konsolda `?` belgilar | cp866 kod sahifasi | `$env:PYTHONUTF8=1` yoki faylga yo'naltirish |

## Hozirgi holat (2026-11-05)

* GPU: **NVIDIA GeForce RTX 5070**, 12 GB, driver 610.88 (CUDA 13.3).
* Production: `data/models/equipment_best.pt` **yo'q** → tizim `yolov8s.pt`
  (COCO, 80 sinf) fallback bilan ishlaydi; `YOLO_DEVICE=cpu`.
* Shuning uchun 27 sinfning katta qismi (printer, UPS, LAN tester, IP telefon,
  tizim bloki...) hozircha **umuman aniqlanmaydi**.

## O'qitish natijasi (2026-11-05)

| Bosqich | Natija |
|---|---|
| O'qitish | `candidate_20261105_150308` — yolo26m, 77/100 epoch (early stop), 11.0 daqiqa, RTX 5070 |
| Dataset | train 282 / val 84 kadr, 42 dublikat karantinga ajratilgan, `Xatolar: 0` |
| Val sifat | mAP50 **0.7004**, mAP50-95 0.5631, P 0.8157, R 0.6411 |
| Ilova mosligi | `integration_check.py` — 27 sinf, `person=0`, `detect()`, ByteTrack: **PASS** |
| Regression | `pytest -q` → **281 passed**, tizim qayta ishga tushirildi, kamera ONLINE |
| Qaror | **productionga chiqarilmadi** — `data/models/equipment_candidate.pt` da nomzod sifatida turadi |

Nega chiqarilmadi (batafsil: `reports/MODEL_CARD.md`, `reports/REGRESSION.md`):

1. `person` recall **0.681** — eski `yolov8s` da **0.894** (tizimning asosiy signali);
2. CPU kechikish **124.9 ms** vs 63.7 ms → **1.96×** (chegara 1.50×);
3. 27 sinfdan **18 tasiga birorta kadr yo'q** — almashtirilsa, hozir COCO fallback
   orqali tutilgan obyektlar yo'qoladi.

Almashtirish sharti: `person recall ≥ 0.89` **va** kechikish nisbati `≤ 1.5×`
bo'lguncha `export.py --switch` ishlatilmaydi.
