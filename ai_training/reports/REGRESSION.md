# Regression hisoboti — custom model ishi yakuni (2026-11-05)

Maqsad: yangi modelni tayyorlash jarayonida **ishlayotgan tizim buzilmaganini**
isbotlash. Barcha tekshiruvlar production muhitida (`.venv`, `YOLO_DEVICE=cpu`,
`.env` o'zgarmagan) bajarildi.

## 1. Kod holati

| Tekshiruv | Natija |
|---|---|
| Git HEAD | `9c853d5` (o'zgarmagan) |
| Git checkpoint | tag `checkpoint-before-custom-model-2026-11-05` |
| Kuzatilayotgan fayllar | **0 ta o'zgargan** (`git status` faqat `?? ai_training/` yangi papkani ko'rsatadi) |
| `app/`, `scripts/`, `tests/` | tegmagan |
| `.env` | tegmagan (`YOLO_MODEL_PATH=data/models/equipment_best.pt`) |
| `data/inventory.db` | reset qilinmagan, o'chirilmagan |

## 2. Testlar

```
.venv\Scripts\python -m pytest -q
281 passed, 1 warning in 45.79s
```

Yakuniy qayta yugurish (barcha hujjat o'zgarishlaridan keyin): `281 passed, 1 warning in 44.95s`.

Baseline (ish boshlanishida): `281 passed ... 46.02s` — **bir xil**, regressiya yo'q.
Ogohlantirish — `anyio.abc.BlockingPortal` deprecation, ish boshidanoq mavjud edi.

## 3. Server qayta ishga tushirildi (asl usulda)

```
.venv\Scripts\python run.py
```

`data/server_stdout.log`:

```
Ishga tushirilmoqda: development (Asia/Tashkent)
Konfiguratsiya: Maxsus YOLO modeli topilmadi: ...\data\models\equipment_best.pt.
               Umumiy 'data/models/yolov8s.pt' modeli ishlatiladi      <- avvalgidek
Database schema ready (10 tables)                                    <- DB buzilmagan
Deteksiya qoidalari yangilandi: 82 sinf
Face galereyasi yuklandi: 5 embedding
Kamera ishga tushdi: 101 (usb)
Kamera 101 holati: ONLINE (Ulanish o'rnatildi)
Pipeline ishga tushdi: 101
Tizim tayyor: http://127.0.0.1:8000
```

## 4. Funktsional tekshiruvlar (jonli server)

| Tekshiruv | Natija |
|---|---|
| `GET /healthz` | `200 {"status":"ok","monitoring":true,"cameras_running":1}` |
| Login (`/login`, CSRF) | `200` → `/` |
| UI `/` (dashboard) | `200`, 11 848 bayt |
| UI `/live` | `200`, 8 609 bayt |
| UI `/review` | `200`, 525 479 bayt (PENDING navbati ochiladi) |
| UI `/inventory` | `200`, 46 859 bayt |
| UI `/employees` | `200`, 6 347 bayt |
| UI `/cameras` | `200`, 11 716 bayt |
| UI `/settings` | `200`, 26 701 bayt |
| UI `/movements` | `200`, 52 092 bayt |
| UI `/data` | `200`, 9 438 bayt |
| UI `/reports` | `200`, 233 190 bayt |
| UI `/sheets` | `200`, 13 809 bayt |
| UI `/training` | `200`, 18 358 bayt |
| UI `/downloads` | `200`, 9 623 bayt |
| UI `/equipment` | `200`, 65 533 bayt |
| Kamera 101 (`/api/cameras/101/status`) | `ONLINE`, `frames_processed 3523`, `detection_errors 0`, `processing_errors 0`, `tracks 2` |
| Snapshot (`/api/cameras/101/snapshot`) | `200`, `image/jpeg`, 31 718 bayt |
| YOLO inference + overlay | kadrda `#1 Noma'lum shaxs` qutisi, kamera nomi, virtual chiziq chizilgan |
| Face service | `buffalo_l`, CPUExecutionProvider — yuklandi |
| ByteTrack | `integration_check.py` → `[PASS] ByteTrack (use_tracking=True)` |

## 5. DB invariantlari (faqat o'qish, `mode=ro`)

| Jadval | Ish boshida | Hozir | Izoh |
|---|---|---|---|
| `cameras` | 1 | 1 | kamera sozlamasi saqlangan |
| `employees` | 1 | 1 | xodim saqlangan |
| `equipment_categories` | 27 | 27 | 27 sinf |
| `face_embeddings` | 5 | 5 | biometrik ma'lumot saqlangan |
| `equipment_assets` | 0 | 0 | — |
| `events` | 86 | 204 | kamera 3 soat ishlagani uchun o'sdi (normal) |
| `inventory_transactions` | 25 | 42 | faqat qo'shildi |
| `users` | 1 | 2 | `doniyor_dxm` — foydalanuvchi o'zi qo'shgan (09:02) |

Qo'shimcha invariantlar:

| Invariant | Natija |
|---|---|
| `PENDING` event tasdiqlangan ledger qatoriga aylanganmi | **0 ta** (`join` bo'yicha tekshirildi) |
| Ledger'dagi barcha qatorlar holati | 42 qator, `is_confirmed = 0` (tasdiqlanmagan) |
| Event'ga bog'lanmagan ledger qatori | 0 |
| Takrorlangan `dedupe_key` | 0 → deduplikatsiya ishlaydi |
| `PENDING` / `AUTO` event'lar | 78 / 127 |

## 6. Eski va yangi model — bir xil kadrlarda

`reports/benchmark_20261105_152937.md` (84 kadr):

| Ko'rsatkich | `yolov8s` (eski) | `yolo26m` candidate |
|---|---|---|
| `person` recall | 0.894 | 0.681 |
| CPU kechikish | 63.7 ms | 124.9 ms |

Nomzod **productionga qo'yilmadi** — `data/models/equipment_candidate.pt` da turadi.
Batafsil: `reports/MODEL_CARD.md`.

## 7. Tekshirilmagan / cheklangan qismlar (yashirilmaydi)

1. **Google Sheets integratsiyasi jonli sinovdan o'tkazilmadi** — `sync_logs` bo'sh,
   hisob sozlamalari (`GOOGLE_*`) o'zgartirilmadi. Kod va konfiguratsiya tegilmagan;
   sinxronizatsiya jadvali (`sheets_sync_status`) o'z joyida.
2. **Operator approval oqimi UI orqali qo'lda bosilmadi** — bu inventar tarixini
   o'zgartiradi (ledger yoziladi). Mantiq `tests/test_events.py` va
   `tests/test_inventory.py` orqali qoplandi (281 test ichida).
3. Datasetdagi kamerа kadrlari **chizilgan** (banner + virtual chiziq) — `/snapshot`
   shunday qaytaradi; label'lar chiziqni chetlab o'tadi. Xom kadr eksporti asosiy
   kodga tegishni talab qiladi, shuning uchun qilinmadi.
4. Val to'plami kichik (84 kadr) — metrikalar keng ishonch oralig'iga ega.

## 8. O'zgargan / qo'shilgan fayllar

Faqat yangi `ai_training/` papkasi (asosiy kodga tegilmagan):

| Fayl | Nima uchun |
|---|---|
| `ai_training/README.md` | izolyatsiya, muhit, ish oqimi, qoidalar |
| `ai_training/configs/training.yaml` | giperparametrlar + qabul chegaralari |
| `ai_training/scripts/collect_frames.py` | kameradan xavfsiz kadr yig'ish (ilova API'si orqali) |
| `ai_training/scripts/prepare_dataset.py` | raw → `images/train`, dedupe, manifest |
| `ai_training/scripts/dedupe_split.py` | splitlar orasidagi/ichidagi dublikatlarni karantinga ajratish |
| `ai_training/scripts/validate_labels.py` | label QA + `reports/label_review.csv` |
| `ai_training/scripts/train.py` | CUDA tekshiruvi, o'qitish, resume, arxivlash |
| `ai_training/scripts/evaluate.py` | val/test metrikasi + qabul chegaralari |
| `ai_training/scripts/benchmark.py` | eski/yangi modelni bir xil kadrlarda taqqoslash |
| `ai_training/scripts/export.py` | candidate saqlash, `--switch` / `--rollback` |
| `ai_training/scripts/integration_check.py` | model ↔ ilova kodi (27 sinf, `detect`, ByteTrack) |
| `ai_training/reports/*` | o'qitish loglari, eval, benchmark, model kartasi, shu hisobot |

Yangi ma'lumot/artefakt (`data/` — gitignore):

* `data/backups/pre-custom-model-2026-11-05/` — ish boshidagi `.env`, DB, model, biometriya;
* `data/models/equipment_candidate.pt` + `.json` — nomzod model;
* `ai_training/dataset/` — kadrlar, label'lar, karantin, manifestlar (gitignore);
* `ai_training/runs/`, `ai_training/models/` — checkpointlar (gitignore).

## 9. Rollback

```powershell
# model nomzodini ishlatib ko'rish (avval .env.bak-<vaqt> olinadi)
ai_training\.venv\Scripts\python ai_training\scripts\export.py --switch --force
.venv\Scripts\python run.py

# eski holatga qaytish
ai_training\.venv\Scripts\python ai_training\scripts\export.py --rollback
.venv\Scripts\python run.py
```

To'liq orqaga qaytish (DB bilan): `.env` va `data/models/` ni
`data/backups/pre-custom-model-2026-11-05/` dan tiklash (`MANIFEST.txt` ichida
sha256 va tartib yozilgan) yoki `git checkout checkpoint-before-custom-model-2026-11-05`.
