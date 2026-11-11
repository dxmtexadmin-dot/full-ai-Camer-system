# Model kartasi — `equipment_candidate.pt`

**Xulosa: model productionga chiqarilmadi.** Nomzod sifatida saqlangan, asosiy
model (`data/models/yolov8s.pt` fallback) o'z joyida. Sabablari quyida 5-bo'limda.

---

## 1. O'qitish

| Parametr | Qiymat |
|---|---|
| Run | `candidate_20261105_150308` |
| Boshlang'ich checkpoint | `yolo26m.pt` (pretrained, `ai_training/models/`) |
| Dataset | `ai_training/dataset/data.yaml` (27 sinf) |
| GPU | NVIDIA GeForce RTX 5070, 12 GB (sm_120), torch 2.11.0+cu128 |
| CUDA | 12.8 — `CUDA_AVAILABLE True` (tekshirilgan, xato yashirilmagan) |
| Batch / imgsz | 16 / 640 (VRAM cho'qqisi 9.03 GB) |
| Epochs | 100 rejalashtirilgan, **77 da to'xtadi** (patience 25) |
| Vaqt | 11.0 daqiqa (~8.6 s/epoch) |
| AMP / cos_lr / seed | True / True / 0 |
| Parametrlar | 20.37 M |
| Saqlangan | `runs/candidate_20261105_150308/weights/{best,last}.pt` + nusxa `ai_training/models/candidate_20261105_150308/` |
| Loglar | `reports/train2_stdout.log`, `runs/.../results.csv`, `runs/.../plots/` |

Resume: `ai_training\.venv\Scripts\python ai_training\scripts\train.py --resume ai_training/runs/<run>`

## 2. Dataset

| Manba | Tavsif |
|---|---|
| Kamera 101 | `/api/cameras/101/snapshot` orqali 2 sessiya: `session_20261105_140831` (150 kadr), `session_20261105_141652` (130 kadr) → dedupe'dan keyin **177 noyob kadr** |
| Ochiq dataset | `hhh134/YOLO-DeskDataset` (loyihaning `scripts/import_public_dataset.py`) — 8 sinf: laptop, mouse, keyboard, monitor, smartphone, tablet, printer, speaker |

Yakuniy holat: **train 282 kadr / val 84 kadr**, hammasida label bor
(3 tasi ataylab bo'sh — obyektsiz fon kadrlari).

### 2.1 Sizib chiqish (leakage) tozalandi

Birinchi yig'ilgan datasetda train va val o'rtasida **35 ta takror kadr** bor edi
(6 aynan bir xil + yaqin dublikatlar) — asosan ochiq datasetning o'z train/val
bo'linmasidan. Bunday holatda val metrikasi sun'iy yuqori chiqadi.

* `scripts/dedupe_split.py` yozildi: sha256 + dHash (Hamming < 3);
* 35 kadr `dataset/quarantine/20261105_150242/` ga **ko'chirildi** (o'chirilmadi)
  + `duplicates.csv` (nima nimaga mos tushgani);
* val ichidagi 7 dublikat ham `quarantine/20261105_152823/` ga ko'chirildi;
* model **tozalangan datasetda qayta o'qitildi** (`candidate_20261105_144007`
  runi sizib chiqqan dataset bilan qilingan va faqat tarix uchun qoldirilgan);
* tekshiruv: `dedupe_split.py` → `Jami 0 ta dublikat`, `validate_labels.py` → `Xatolar: 0`.

### 2.2 Sinf qamrovi

| Holat | Sinflar | Soni |
|---|---|---|
| O'qitiladi | person, monitor, laptop, keyboard, mouse, printer, speaker, smartphone, tablet | 9 |
| Ma'lumot yo'q | all_in_one_pc, desktop_pc, computer_case, scanner, ups, network_device, other_equipment, projector, ip_phone, landline_phone, camera, mini_camera, lan_tester, lan_cable, external_hdd, flash_drive, server, tv | 18 |

18 sinf uchun **bitta ham kadr yo'q** — bu model ularni umuman aniqlay olmaydi.

## 3. Val bo'limidagi sifat (84 kadr, CPU)

mAP50 **0.7004** · mAP50-95 **0.5631** · precision 0.8157 · recall 0.6411

| Sinf | mAP50-95 | Recall |
|---|---|---|
| monitor | 0.7934 | 0.9667 |
| tablet | 0.7718 | 0.8730 |
| person | 0.7611 | 0.7021 |
| laptop | 0.7374 | 0.8416 |
| keyboard | 0.6839 | 0.8064 |
| mouse | 0.6328 | 0.8857 |
| speaker | 0.5475 | 0.6471 |
| printer | 0.5467 | 0.5455 |
| smartphone | 0.1565 | 0.1429 |
| tv | 0.0 | 0.0 (val'da 1 obyekt) |

Hisobot: `reports/eval_val_20261105_152905.{md,json}`

## 4. Production model bilan taqqoslash (bir xil 84 kadr, CPU)

| Ko'rsatkich | Baseline `yolov8s.pt` | Candidate `yolo26m` |
|---|---|---|
| Sinf soni | 80 (COCO) | 27 (loyiha) |
| Parametrlar | 11.2 M | 21.8 M |
| CPU kechikish (o'rt.) | 63.7 ms | 124.9 ms |
| CPU kechikish (p95) | 101.2 ms | 239.5 ms |
| Aniqlangan obyekt | 288 | 202 |
| `person` recall (IoU 0.5, conf 0.35) | **0.894** | **0.681** |

Kechikish nisbati: **1.96×** (ruxsat etilgan chegara 1.50×).
Hisobot: `reports/benchmark_20261105_152937.{md,json}`

Taqqoslash production sharoitida o'tkazildi: `YOLO_DEVICE=cpu`, `imgsz=640`,
`conf=0.35` — `.env` dagi qiymatlar bilan.

## 5. Xulosa — nega productionga chiqarilmadi

1. **`person` recall baseline'dan past**: 0.681 vs 0.894. Tizimning asosiy
   signali (kirish/chiqish, davomat, xavfsizlik) shu sinfga tayanadi — uni
   yomonlashtirish "yaxshilanish" emas.
2. **CPU kechikish 1.96×** — video oqim `PROCESSING_FPS=10` da ishlaydi;
   124.9 ms/kadr byudjetni buzadi (119 ms dan keyin navbat o'sadi).
3. **27 sinfdan 18 tasi o'qitilmagan** — almashtirilsa, hozir fallback model
   (COCO) orqali ma'lum darajada tutiladigan obyektlar butunlay yo'qoladi.
4. Sizib chiqqan dataset bilan olingan dastlabki yuqori raqamlar (mAP50 0.758)
   tozalashdan keyin tasdiqlanmadi — model hali "tayyor" emas.

Model mosligi (API/tracking) nuqtai nazaridan **to'liq tayyor**: `integration_check.py`
barcha tekshiruvlardan o'tdi (27 sinf, `person=0`, `detect()`, ByteTrack) — ya'ni
kelajakda dataset to'ldirilgach, kod o'zgartirmasdan almashtirish mumkin.

## 6. Artefaktlar

| Fayl | Izoh |
|---|---|
| `data/models/equipment_candidate.pt` | 44 148 087 bayt, `sha256 c8dd8a05c5ed78e257c6ad39a8dd87845d2245ecd0dd7a7e2d26e70cbd7c27e7` |
| `data/models/equipment_candidate.json` | metrikalar + manba + rollback yo'riqnomasi |
| `ai_training/models/candidate_20261105_150308/{best,last}.pt` | arxiv nusxalari |
| `ai_training/dataset/manifests/dataset_20261105_153011.json` | dataset versiyasi (sha256) |

## 7. Yoqish / qaytarish

`.env` **o'zgartirilmadi** (`YOLO_MODEL_PATH=data/models/equipment_best.pt`,
fayl yo'q → `yolov8s.pt` fallback).

```powershell
# sinov rejimida yoqish (avval .env.bak-<vaqt> nusxasi olinadi)
ai_training\.venv\Scripts\python ai_training\scripts\export.py --switch --force
# serverni qayta ishga tushirish
.venv\Scripts\python run.py
# qaytarish
ai_training\.venv\Scripts\python ai_training\scripts\export.py --rollback
```

Qo'shimcha himoya: model yuklanmasa yoki fayl topilmasa, `YoloService`
`YOLO_FALLBACK_MODEL` ga o'zi qaytadi — tizim "ko'r" bo'lib qolmaydi.

## 8. Keyingi qadam (productionga chiqarish uchun minimal shartlar)

1. 18 bo'sh sinf uchun kadr yig'ish (avvalo `lan_cable`, `ups`, `computer_case`,
   `desktop_pc`, `camera`, `flash_drive`, `external_hdd`, `server`).
2. `smartphone` (mAP 0.16) va `printer` (0.55) uchun qo'shimcha kadr.
3. Inson ko'rigidan o'tgan label'lar (`reports/label_review.csv`).
4. Qayta o'qitish + `benchmark.py`: `person recall ≥ 0.89` va kechikish
   nisbati `≤ 1.5×` bo'lguncha almashtirish taqiqlanadi.
