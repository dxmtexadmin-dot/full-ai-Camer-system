# Baseline — custom model ishi boshlanishidan oldingi holat

Sana: **2026-11-05**. Bu hujjat "ishlayotgan tizim buzilmadi" degan da'voning
asosi: ish boshlanishidan oldin olingan o'lchovlar.

## Git

| Ko'rsatkich | Qiymat |
|---|---|
| Branch | `main` (tracking `origin/main`) |
| HEAD | `9c853d5397f87eeeee58e65266b72adee00900f3` |
| Oxirgi commit | `Add INSTALL.txt: full clone-to-run guide incl. YOLO and AI stack` |
| Ishchi daraxt | toza (o'zgarish yo'q) |
| Checkpoint | **tag**: `checkpoint-before-custom-model-2026-11-05` → shu commit |
| Tracked fayllar | 119 |

`git` `PATH` da yo'q, to'liq yo'l bilan ishlatiladi:
`& 'C:\Program Files\Git\cmd\git.exe' ...`
(annotated tag uchun committer identity sozlanmagan — lightweight tag ishlatildi;
`git config` **o'zgartirilmadi**).

## Runtime zaxira nusxasi

`data/backups/pre-custom-model-2026-11-05/` (gitignore ichida, chatga tushmaydi):

* `inventory.db` — SQLite backup API bilan **server ishlab turgan holda** olingan izchil nusxa;
* `.env` — konfiguratsiya;
* `models/yolov8s.pt` — hozir amalda ishlatilayotgan fallback vaznlar;
* `employees/` — `EMP001` reference rasmlari (biometrik ma'lumot);
* `MANIFEST.txt` — har bir faylning sha256, hajmi va tiklash buyruqlari.

Zaxira nusxadagi DB tarkibi: `events=86`, `inventory_transactions=25`,
`equipment_categories=27`, `employees=1`, `face_embeddings=5`, `users=1`,
`cameras=1`, `audit_log=25`.

## Ishga tushirish (o'zgarmagan)

| Narsa | Qiymat |
|---|---|
| Python | 3.12.10 (`.venv`), README 3.11 talab qiladi — 3.12 da barcha testlar o'tadi |
| Core paketlar | `requirements.txt` (pinned: fastapi 0.115.14, SQLAlchemy 2.0.55, numpy 2.2.6, opencv 4.10.0.84, gspread 6.1.4, ...) |
| AI paketlar | `requirements-ai.txt` (ultralytics 8.4.199, insightface 2.1, onnxruntime 1.20.1, torch 2.14.1+**cpu**) |
| Start buyrug'i | `.venv\Scripts\python run.py` (yoki `run.bat`) → `uvicorn app.main:app` 127.0.0.1:8000 |
| Health | `GET /healthz` → `{"status":"ok","monitoring":true,"cameras_running":1}` |
| Kamera | `101` "Shaxsiy xona", `source_type=usb`, `source=0`, chiziq `320,40 → 320,440`, holat **ONLINE** |
| Model | `data/models/equipment_best.pt` **yo'q** → `YOLO_FALLBACK_MODEL=data/models/yolov8s.pt` (COCO 80 sinf) |
| Yuz | InsightFace `buffalo_l`, onnxruntime CPU |

## Testlar

```
.venv\Scripts\python -m pytest -q
281 passed, 1 warning in 46.02s
```

Ogohlantirish: `starlette/testclient.py` → `anyio.abc.BlockingPortal` deprecation
(uchinchi tomon kutubxonasi, loyiha kodi emas).

## "Buzmaslik" ro'yxati (tekshiriladi)

- [ ] `pytest -q` — 281 passed
- [ ] Server ishga tushadi, `/healthz` javob beradi
- [ ] 14 sahifa 200 qaytaradi
- [ ] Kamera `101` ONLINE, kadr va event'lar oqimi davom etadi
- [ ] YOLO inference + ByteTrack + InsightFace ishlaydi
- [ ] `PENDING` event'lar `/review` da ko'rinadi va ledger'ga faqat tasdiqdan keyin tushadi
- [ ] `data/models/equipment_best.pt` **mavjud emas** (ya'ni production model almashtirilmagan)
- [ ] `.env` dagi `YOLO_MODEL_PATH` — `data/models/equipment_best.pt` (o'zgarmagan)
