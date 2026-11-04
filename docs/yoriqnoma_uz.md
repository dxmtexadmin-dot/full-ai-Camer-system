# AI CCTV: xodimlarni tanish va jihozlar inventarizatsiyasi

**To‘liq foydalanuvchi qo‘llanmasi**

- Loyiha papkasi: `C:\Users\Admin\Desktop\face-date`
- Versiya: 1.0 (2026-yil 28-oktabr)
- Platforma: Windows 22H2, Python 3.11
- Veb-manzil: http://127.0.0.1:8000
- Hujjat tili: o‘zbek (lotin)

> Bu qo‘llanma tizimni **nolldan ishga tushirish**, **har kuni ishlatish**, **texniklarni rasm bilan o‘qitish**,
> **kamera va chiziqni sozlash**, **inventarni yuritish** va **nosozliklarni bartaraf etish** uchun yozilgan.
> Har bir bo‘lim amaliy qadamlar bilan berilgan: buyruqlar, tugmalar nomi va natijani qanday tekshirish.

---

## Mundarija

1. Tezkor start (5 daqiqa)
2. Tizim nima qiladi: asosiy tushunchalar
3. Tizim talablari
4. Papka tuzilmasi va muhim fayllar
5. O‘rnatish
6. `.env` sozlamalari
7. Birinchi ishga tushirish va login
8. Har kuni ishga tushirish (run qilish)
9. Sahifalar bo‘yicha qo‘llanma
10. Xodim va texniklarni rasm bilan o‘qitish
11. Aniqlikni oshirish: amaliy qoidalar
12. Inventar qoidalari (append-only jurnal)
13. Hodisalar, dublikatlar va dalillar
14. Rollar, xavfsizlik va audit
15. Google Sheets: qo‘shish va ulash (3 qadam)
16. Yuklab olish: Excel va CSV
17. CLI skriptlar
18. Testlar
19. Nosozliklarni bartaraf etish
20. Zaxira nusxa va tiklash
21. Halol cheklovlar (nima nimaga bog‘liq)
22. Tez-tez so‘raladigan savollar
23. Ilova: atamalar, statuslar, API
24. Yakuniy nazorat ro‘yxati

---

# 1. Tezkor start (5 daqiqa)

Agar tizim allaqachon o‘rnatilgan bo‘lsa (`.venv` papkasi bor), faqat shu besh qadam kerak:

```bat
cd C:\Users\Admin\Desktop\face-date
.venv\Scripts\python.exe run.py --port 8000
```

1. Konsolda `Tizim tayyor: http://127.0.0.1:8000` yozuvini kuting.
2. Brauzerda http://127.0.0.1:8000 ni oching.
3. Login: `admin` / `Admin123!` (birinchi kirishdan keyin albatta almashtiring).
4. `/cameras` → kamera **ONLINE** ekanini tekshiring.
5. `/employees` → xodim → **Kameradan rasm olish** (2–3 marta) → `/live` da ism chiqishini ko‘ring.

To‘xtatish: konsolda `Ctrl + C`.

---

# 2. Tizim nima qiladi: asosiy tushunchalar

Tizim bitta vazifani bajaradi: **kameradagi odamni tanib, u qanday jihoz bilan kirdi/chiqdi — shuni yozib borish**
va inventarni shu yozuvlar asosida yuritish. Bunda hech narsa “o‘ylab topilmaydi”: har bir yozuv ortida kadr,
trek, ishonch balli va audit qatori turadi.

## 2.1. Ish sxemasi (matnli)

```text
Kamera (RTSP / USB / fayl / simulyatsiya)
   -> YOLO: odam va jihoz qutilarini topadi
   -> ByteTrack: har bir obyektga trek ID beradi (masalan #59)
   -> InsightFace: odam qutisining yuqori qismidan yuz olib, galereya bilan solishtiradi
   -> Chiziq (line): odam chiziqni kesib o‘tganda IN (kirdi) yoki OUT (chiqdi)
   -> Bog‘lanish (association): “qaysi odam qaysi jihozni olib o‘tdi” balli
   -> Hodisa (event): PENDING (tekshirish kerak) yoki AUTO (ishonchli)
   -> Inventar jurnali: tasdiqlangandan keyingina qoldiq o‘zgaradi
   -> Dalil rasmi + audit yozuvi + (ixtiyoriy) Google Sheets
```

## 2.2. Atamalar

| Atama | Ma’nosi |
| --- | --- |
| **Xodim** | Tizimga kiritilgan shaxs: `EMP001`, ism-familiya, bo‘lim, holat (faol/bloklangan) |
| **Shablon (embedding)** | Rasm asosida hisoblangan 512 o‘lchamli vektor. Rasm emas, matematik “imzo” |
| **Galereya** | Barcha faol xodimlarning shablonlari to‘plami (xotirada). 10 shablon = 10 ta imzo |
| **Trek** | Kamera kadrida ketma-ket kuzatilayotgan obyekt (`#12`). Bir odam bitta trekda yuradi |
| **Chiziq (line)** | Kadr ustidagi virtual ikki nuqta. Undan o‘tish “kirdi/chiqdi” deb hisoblanadi |
| **Hodisa (event)** | Yozuv: kim, qachon, qaysi kamera, qaysi toifa, qanday ishonch bilan |
| **Dalil (evidence)** | Hodisa momentidagi JPEG kadr (`data/evidence`) |
| **PENDING** | Tekshirish kutayotgan hodisa. Inventarga hali ta’sir qilmaydi |
| **CONFIRMED / REJECTED** | Operator tasdiqladi / rad etdi. Rad etilgani balansga qo‘shilmaydi |
| **Bog‘lanish (association)** | “Kim olib o‘tdi” balli: olib yurish + yaqinlik + vaqt + yo‘nalish |
| **Toifa (category)** | Jihoz turi: `monitor`, `laptop`, `printer`, `ups`, ... (`person` alohida) |
| **Asset** | Aniq jihoz birligi: `MON-001`, brend, model, seriya raqami, joylashuv |
| **Inventar jurnali** | Faqat qo‘shib boriladigan qatorlar (append-only). Qator o‘chirilmaydi, tahrirlanmaydi |
| **Audit** | Kim, qachon, qaysi amalni qildi (`audit_logs`) |
| **Rol** | `admin` (hamma narsa), `operator` (tekshirish, inventar), `viewer` (faqat o‘qish) |

## 2.3. Uchta muhim qoida

1. **Kamera + chiziq + shablon** — uchtasi bo‘lmasa natija bo‘lmaydi. Chiziq yo‘q bo‘lsa kirim/chiqim
   hodisasi yaratilmaydi; shablon yo‘q bo‘lsa odam “Noma’lum shaxs” bo‘ladi; kamera uzilgan bo‘lsa kadr kelmaydi.
2. **Kamera hodisasi inventarni o‘zi o‘zgartirmaydi.** Faqat operator tasdiqlagach qoldiq o‘zgaradi.
3. **Tizim ishlamayotganini yashirmaydi.** Model yo‘q, kamera yo‘q, yuz topilmadi — hammasi ochiq yoziladi
   (dashboard ogohlantirishi, sahifadagi qizil xabar, `logs/app.log`).

---

# 3. Tizim talablari

| Bo‘lim | Talab | Izoh |
| --- | --- | --- |
| OS | Windows 10/11 (22H2 sinalgan), Linux/macOS ham mumkin | Loyiha OS’ga bog‘lanmagan, faqat `run.bat` Windows uchun |
| Python | **3.11** | AI g‘ildiraklari (`insightface`, `onnxruntime`, `torch`) 3.11 uchun tayyor |
| RAM | 8 GB (kamida 4 GB) | YOLO + InsightFace CPU’da ~1.5–2.5 GB |
| CPU | 4 yadro tavsiya | CPU’da 5–10 FPS real |
| Disk | 5 GB bo‘sh joy | `torch` ~1–2 GB, `buffalo_l` ~280 MB, baza va dalillar |
| Kamera | USB (UVC) yoki RTSP IP-kamera | Simulyatsiya va video fayl ham qo‘llanadi |
| Internet | Faqat birinchi o‘rnatishda | `pip` paketlari va `buffalo_l` modeli yuklanadi |
| Brauzer | Chrome / Edge / Firefox | MJPEG oqim uchun |

> **Nega aynan Python 3.11?** `insightface` ning Windows uchun tayyor g‘ildiragi va `onnxruntime`,
> `torch` bilan mosligi shu versiyada eng barqaror. 3.12/3.13 da C++ kompilyator talab qilishi mumkin.

---

# 4. Papka tuzilmasi va muhim fayllar

```text
face-date\
├── app\                     # tizim kodi
│   ├── api\                 # HTTP qatlami: sahifalar, formalar, JSON API
│   ├── services\            # biznes-mantiq: kamera, YOLO, yuz, pipeline, inventar
│   ├── models\              # ma’lumotlar bazasi modellari (SQLAlchemy)
│   ├── ui\templates\        # Jinja2 shablonlar (10 sahifa)
│   └── ui\static\           # app.css, app.js
├── scripts\                 # CLI: xodim qo‘shish, kamera test, YOLO o‘qitish, Sheets
├── tests\                   # pytest to‘plami
├── data\
│   ├── inventory.db         # SQLite baza (asosiy ma’lumot)
│   ├── employees\<KOD>\     # shablon rasmlari: reference_01.jpg ...
│   ├── evidence\            # dalil kadrlari (kunlik papkalar)
│   ├── samples\<toifa>\     # o‘qitish uchun namuna rasmlar (/training)
│   └── models\              # equipment_best.pt (custom YOLO) — bo‘lmasa fallback
├── dataset\                 # o‘qitish to‘plami (--init bilan yaratiladi; gitga tushmaydi)
│   ├── data.yaml            # 27 sinf tartibi
│   ├── images\train|val\    # kadrlar
│   ├── labels\train|val\    # YOLO .txt qutilar
│   └── preview\train|val\   # autolabel chizgan tekshiruv nusxalari
├── logs\                    # app.log, stdout/stderr
├── docs\                    # qo‘shimcha hujjatlar (YOLO o‘qitish, shu qo‘llanma)
├── .env                     # lokal sozlamalar (git’ga tushmaydi)
├── .env.example             # namuna
├── requirements.txt         # asosiy paketlar
├── requirements-ai.txt      # AI paketlari (ultralytics, insightface, onnxruntime)
├── run.bat / run.sh         # tez ishga tushirish
└── run.py                   # server CLI (--host, --port, --reload, --log-level)
```

**Nimani qo‘lda o‘chirmaslik kerak:** `data\inventory.db` (baza), `data\employees` (shablon rasmlari),
`data\evidence` (dalillar). Bular o‘chsa — tarix va shablonlar yo‘qoladi.

---

# 5. O‘rnatish

## 5.1. Eng oson yo‘l (Windows)

```bat
cd C:\Users\Admin\Desktop\face-date
run.bat
```

Skript: `.venv` yaratadi → `.env` ni `.env.example` dan nusxalaydi → `requirements.txt` ni o‘rnatadi →
serverni ko‘taradi. Birinchi ishga tushirishdan **oldin** `.env` dagi `APP_SECRET_KEY` va
`BOOTSTRAP_ADMIN_PASSWORD` ni o‘zgartiring.

## 5.2. Qo‘lda o‘rnatish

```bat
cd C:\Users\Admin\Desktop\face-date
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python.exe run.py
```

## 5.3. AI paketlari (real deteksiya va yuz tanish uchun)

Dashboard, inventar va tekshirish AI paketlarisiz ham ishlaydi — lekin kamera hech kimni tanimaydi.

```bat
.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
```

- `insightface` **2.1** versiyasi ishlatiladi (tayyor `py3-none-any` g‘ildirak, C++ kompilyator kerak emas).
  0.7.x ni tanlamang — Windows’da “Visual Studio Build Tools” talab qiladi.
- `buffalo_l` modeli (~280 MB) birinchi ishlatishda yuklanadi va
  `C:\Users\<user>\.insightface\models\buffalo_l` ichida saqlanadi. Internetsiz mashinaga shu papkani
  ko‘chirib qo‘yish mumkin.
- Paketlar o‘rnatilgach **serverni qayta ishga tushirish shart** (import jarayon boshida bo‘ladi).
- CUDA uchun: `onnxruntime-gpu` va `.env` da `FACE_PROVIDERS=CUDAExecutionProvider`.

**Tekshirish:** `/settings` → “Yuz modeli: mavjud”, `logs/app.log` da
`Face galereyasi yuklandi: N embedding`.

## 5.4. YOLO modeli

- Tayyor holatda `yolov8s.pt` (COCO) ishlatiladi: `monitor`, `laptop` kabi sinflarni biladi.
- Ish joyi jihozlari (`printer`, `ups`, `scanner`, `computer_case`) uchun custom model kerak —
  `docs/yolo_training.md` va `scripts/train_yolo.py`.
- Custom model `data/models/equipment_best.pt` ga qo‘yiladi; bo‘lmasa dashboard’da
  `Maxsus YOLO modeli topilmadi` ogohlantirishi turadi (bu **xato emas**, demo rejimi).
- Modelni shu xonaga moslash uchun `/training` sahifasida haqiqiy rasmlar yig‘iladi (9.14),
  keyin `train_yolo.py --import-samples` bilan datasetga qo‘shiladi va qutilar
  `autolabel_dataset.py` bilan oldindan chiziladi (11.2).

---

# 6. `.env` sozlamalari

| Kalit | Ma’nosi | Tavsiya |
| --- | --- | --- |
| `APP_ENV` | `development` / `production` | `production` da cookie faqat HTTPS |
| `APP_HOST`, `APP_PORT` | Server manzili | `127.0.0.1`, `8000` |
| `APP_TIMEZONE` | Mahalliy vaqt | `Asia/Tashkent` |
| `DATABASE_URL` | Baza yo‘li | `sqlite:///./data/inventory.db` |
| `APP_SECRET_KEY` | Sessiya imzo kaliti | **Albatta o‘zgartiriladi** (uzun tasodifiy satr) |
| `SESSION_MAX_AGE_SECONDS` | Sessiya muddati | `43200` (12 soat) |
| `BOOTSTRAP_ADMIN_*` | Birinchi admin | Faqat bo‘sh bazada yaratiladi |
| `YOLO_MODEL_PATH` | Custom model | `data/models/equipment_best.pt` |
| `YOLO_FALLBACK_MODEL` | Zaxira model | `yolov8s.pt` |
| `YOLO_CONFIDENCE` | Deteksiya chegarasi | `0.35` |
| `YOLO_IMAGE_SIZE` | Inference o‘lchami | `640` (sekin CPU’da `480`) |
| `YOLO_DEVICE` | `cpu` / `cuda:0` | `cpu` |
| `FACE_MODEL_NAME` | Yuz modeli | `buffalo_l` |
| `FACE_SIMILARITY_THRESHOLD` | Moslik chegarasi | `0.45` (0.40–0.55 oralig‘ida sinang) |
| `FACE_MATCH_MARGIN` | 1- va 2-nomzod farqi | `0.05` |
| `FACE_PROVIDERS` | ONNX provayder | `CPUExecutionProvider` |
| `FACE_DETECTION_SIZE` | Yuz deteksiya o‘lchami | `640x640` |
| `FACE_RECOGNITION_INTERVAL` | Har necha kadrda yuz tekshiriladi | `10` |
| `PROCESSING_FPS` | Qayta ishlash chastotasi | `10` (CPU’da 5–8) |
| `PROCESSING_WIDTH`, `PROCESSING_HEIGHT` | Kadr o‘lchami | `1280x720` — **chiziq koordinatalari shu o‘lchamda** |
| `CAMERA_RECONNECT_INTERVAL`, `CAMERA_MAX_RETRY_INTERVAL` | Qayta ulanish | `5`, `60` |
| `MAX_FRAME_QUEUE` | Navbat uzunligi | `4` |
| `SIMULATION_MODE` | Sintetik manba | `false` |
| `EVENT_COOLDOWN_SECONDS` | Dublikat oynasi | `20` |
| `EVIDENCE_ENABLED`, `EVIDENCE_RETENTION_DAYS`, `EVIDENCE_JPEG_QUALITY` | Dalillar | `true`, `30`, `85` |
| `GOOGLE_SHEETS_*` | Sheets integratsiyasi | Standart `false` |
| `LOG_LEVEL`, `LOG_DIR` | Loglar | `INFO`, `logs` |

**Muhim:** `APP_SECRET_KEY`, `DATABASE_URL` va bootstrap parolini `/settings` sahifasidan tahrirlab
bo‘lmaydi (ataylab). Ularni faqat `.env` faylida o‘zgartiriladi va server qayta ishga tushiriladi.

---

# 7. Birinchi ishga tushirish va login

1. `run.bat` yoki `.venv\Scripts\python.exe run.py` ni ishga tushiring.
2. Baza avtomatik yaratiladi (`data/inventory.db`): 27 ta toifa (`person` + 26 jihoz toifasi) va
   **o‘chirilgan** namuna kamera `SIM-01`. Yangi toifalar mavjud bazaga ham startda qo‘shiladi.
3. Brauzerda http://127.0.0.1:8000 → login sahifasi.
4. Login: `.env` dagi `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` (standart `admin` / `Admin123!`).
   Admin faqat **foydalanuvchilar jadvali bo‘sh bo‘lganda** yaratiladi.
5. `/settings` → **Foydalanuvchilar**: operator va kuzatuvchi hisoblarini yarating, admin parolini almashtiring.
6. Bosh sahifadagi konfiguratsiya ogohlantirishlarini yo‘q qiling:
   `APP_SECRET_KEY o‘zgartirilmagan`, `Maxsus YOLO modeli topilmadi` va h.k.
7. `/healthz` — tashqi nazorat uchun JSON holat: `{"status":"ok","monitoring":true,"cameras_running":1}`.

---

# 8. Har kuni ishga tushirish (run qilish)

## 8.1. Oddiy usul

```bat
cd C:\Users\Admin\Desktop\face-date
.venv\Scripts\python.exe run.py --port 8000
```

Konsolni **yopmang** — server shu jarayonda ishlaydi. To‘xtatish: `Ctrl + C`.

## 8.2. Fon rejimida (konsol yopilsa ham ishlashi uchun)

```powershell
Start-Process -FilePath ".\.venv\Scripts\python.exe" `
  -ArgumentList "run.py","--port","8000" `
  -WorkingDirectory "C:\Users\Admin\Desktop\face-date" `
  -RedirectStandardOutput "logs\stdout.log" `
  -RedirectStandardError  "logs\stderr.log" `
  -WindowStyle Hidden
```

Tekshirish: `(Invoke-WebRequest http://127.0.0.1:8000/healthz -UseBasicParsing).Content`

Portni band qilgan jarayonni topish va to‘xtatish:

```powershell
netstat -ano | Select-String ':8000.*LISTENING'
Stop-Process -Id <PID> -Force
```

## 8.3. Ishlab chiqish rejimi

```bat
.venv\Scripts\python.exe run.py --reload
```

Kod o‘zgarsa server o‘zi qayta yuklanadi. **Diqqat:** `--reload` bilan ikkita jarayon ishlaydi; AI modeli
ikki marta yuklanadi va kamera qurilmasi band bo‘lib qolishi mumkin — kundalik ishda ishlatmang.

## 8.4. Muhim qoidalar

- **Bitta server** — bitta baza. Ikki server bir vaqtda ishga tushsa `database is locked` xatosi chiqadi.
- Kamera uzilsa server o‘zi qayta ulanadi (5 → 10 → 20 → 40 → 60 s). Qayta ishga tushirish shart emas.
- Kompyuter qayta yuklansa — `run.py` ni yana ishga tushirish kerak (xizmat sifatida o‘rnatilmagan).

---

# 9. Sahifalar bo‘yicha qo‘llanma

Barcha sahifalar yuqoridagi menyuda. Rol bo‘yicha kirish huquqi 14-bo‘limda.

## 9.1. Bosh sahifa (`/`)

Nima ko‘rinadi: bugungi hodisalar soni, kameralar holati, inventar ogohlantirishlari, konfiguratsiya
ogohlantirishlari (model yo‘q, kalit o‘zgarmagan va h.k.).

Nima qilinadi: har kuni ish boshida shu sahifadan boshlang — **ogohlantirish bo‘lsa, avval shuni tuzating.**

## 9.2. Jonli kamera (`/live`)

- MJPEG oqim (annotatsiya bilan): odam qutilari, trek ID, ism, chiziq.
- Kamera holati: `CONNECTING → ONLINE` yoki `OFFLINE` + sabab.
- Ism kadrda ko‘rinmasa: `/employees` da shablonni tekshiring, `FACE_SIMILARITY_THRESHOLD` ni
  `0.42` ga tushirib ko‘ring, keyin kameradan rasm qo‘shing (10-bo‘lim).
- Skrinshot: `GET /api/cameras/<KOD>/snapshot`; oqim: `GET /api/cameras/<KOD>/stream`.

## 9.3. Xodimlar (`/employees`, faqat admin)

Ro‘yxat: kod, ism, bo‘lim, shablonlar soni, holat. Qidiruv va “faqat faol” filtri bor.

**Yangi xodim:** “Xodim qo‘shish” → ism, familiya, bo‘lim, tabel raqami → **Saqlash**.
Xodim kodi bo‘sh qoldirilsa `EMP###` avtomatik beriladi. Saqlangach xodim sahifasiga o‘tadi va
“kamida 2 ta aniq rasm yuklang” xabari chiqadi.

**Xodim kartasi** (`/employees/EMP002`):

| Blok | Nima qilinadi |
| --- | --- |
| Ma’lumotlarni tahrirlash | Ism, familiya, bo‘lim, tabel, izoh |
| Holatni o‘zgartirish | **Bloklash** — galereyadan chiqadi, tarix saqlanadi |
| Yuz shablonlari | Mavjud rasmlar, har birini **✕** bilan o‘chirish (auditga yoziladi) |
| Yangi rasmlar (2–5 ta) | Kompyuterdan fayl yuklash → “Yuklash va shablon yaratish” |
| **Kameradan rasm olish** | Jonli kadr + tugma (eng tez aniqlik oshiradigan usul) |
| Xodimni o‘chirish | Kodni tasdiqlash bilan butunlay o‘chirish (shablon + rasm papkasi) |

Qoidalar:

- Bitta xodim uchun **jami 15 tagacha** rasm. Chegaradan oshsa: `Chegara: bitta xodim uchun 15 tagacha...`
- Bir xil fayl (baytma-bayt) ikkinchi marta saqlanmaydi: `... bu rasm avval yuklangan, qayta saqlanmadi.`
- Tugma bosilganda “Yuborilmoqda...” bo‘lib bloklanadi — forma tasodifan ikki marta yuborilmaydi.

## 9.4. Kameralar (`/cameras`, faqat admin)

| Maydon | Izoh |
| --- | --- |
| Kod | Qisqa unikal kod: `CAM`, `CAM-01` (harf/raqam/`-`/`_`) |
| Nom, joylashuv | Odam o‘qiydigan tavsif |
| Manba turi | `rtsp`, `usb`, `file`, `simulation` |
| Manba | RTSP URL / qurilma raqami / fayl yo‘li |
| Chiziq `x1,y1,x2,y2` | Virtual chiziq (piksellarda, `PROCESSING_*` o‘lchamida) |
| Chiziqni teskari | KIRDI/CHIQDI yo‘nalishini almashtiradi |
| Ishonch chegarasi | Shu kamera uchun yakka chegara (bo‘sh = global) |
| Yoqilgan | Saqlanganda darhol ishga tushadi |

Manba namunalari:

```text
rtsp://admin:parol@192.168.1.64:554/Streaming/Channels/101
usb:0
C:\video\kirish.mp4
```

**Chiziqni qanday qo‘yish kerak (amaliy):**

1. Kadr o‘lchamini aniqlang: `PROCESSING_WIDTH` × `PROCESSING_HEIGHT` (standart `1280×720`).
2. Chiziq **yurish yo‘nalishiga perpendikulyar**, eshikdan 1–1.5 m ichkarida bo‘lsin.
3. Gorizontal chiziq misoli (1280×720, ekran balandligining 62 %): `102,446,1177,446`.
4. Agar “kirdi” va “chiqdi” teskari chiqsa — **“Chiziqni teskari”** katagini yoqing.
5. Chiziq saqlangach kamera qayta ishga tushadi va o‘tishlar hodisa bo‘lib yoziladi.

**Kadrni tekshirish uchun yordamchi:**

```bat
.venv\Scripts\python.exe scripts\test_camera.py --source 0 --seconds 8 --save 3
```

Kadrlar `data\debug\` ga saqlanadi — chiziq koordinatalarini shular ustida o‘lchash qulay.

## 9.5. Jihozlar (`/equipment`, faqat admin)

**Yangi jihoz (asset):** toifa, avtomatik yoki qo‘lda inventar kodi (`MON-001`), brend, model,
seriya raqami, inventar teg, joylashuv, holat, izoh.

**Toifalar va YOLO sinflari:** mavjud 27 ta toifani tahrirlash (nom, YOLO sinf nomlari, minimal ishonch,
faol/faol emas). “Shaxs” toifasiga jihoz qo‘shib bo‘lmaydi.

Toifalar ro‘yxati (kod → nomi):

| Guruh | Toifalar |
| --- | --- |
| Kompyuter | `desktop_pc` Stol kompyuteri, `all_in_one_pc` Monoblok, `laptop` Noutbuk, `monitor` Monitor, `keyboard` Klaviatura, `mouse` Sichqoncha, `computer_case` Tizim bloki, `server` Server |
| Ofis texnikasi | `printer` Printer, `scanner` Skaner, `ups` UPS, `speaker` Dinamik, `projector` Proyektor, `tv` Televizor |
| Telefoniya | `ip_phone` IP telefon, `landline_phone` Statsionar telefon, `smartphone` Smartfon, `tablet` Planshet |
| Tarmoq va kamera | `network_device` Tarmoq uskunasi, `camera` Kamera (IP/CCTV), `mini_camera` Mini IP kamera, `lan_tester` LAN tester, `lan_cable` LAN kabel |
| Saqlash | `external_hdd` Tashqi disk, `flash_drive` USB flesh |
| Boshqa | `other_equipment` Boshqa jihoz |

Har bir toifa bir necha sinf nomiga bog‘lanadi (masalan `lan_cable`: `lan_cable`, `network_cable`,
`ethernet_cable`, `patch_cable`, `utp_cable`). **Bir sinf nomi faqat bitta toifada bo‘lishi kerak** —
ikki toifa bir xil nomga da’vo qilsa, startda eng katta `sort_order` li toifa tanlanadi.

**Yangi texnika toifasi** (sahifa ostidagi alohida karta):

| Maydon | Izoh |
| --- | --- |
| Kod | Kichik lotin harflari/raqam/`_`: `drill`, `hammer_drill` |
| Nomi | O‘zbekcha: `Perforator` |
| YOLO sinflari | Vergul bilan: `drill, hammer drill` (modeldagi sinf nomlari bilan **aynan** mos bo‘lishi kerak) |
| Minimal ishonch | `0.05–0.99` (bo‘sh = global) |
| Izoh | Ixtiyoriy |

Mavjud toifalar **o‘zgarmaydi**, yangi qator ro‘yxat oxiriga qo‘shiladi va `category.create` bo‘lib
auditga yoziladi. **Muhim:** yangi toifa faqat YOLO modeli o‘sha sinfni tanisa ishlaydi. Modelni qayta
o‘qitmasdan turib, yangi toifa “qog‘ozda” qoladi.

## 9.6. Inventar (`/inventory`)

Yuqorida qoldiqlar jadvali, pastda jurnal (200 qator) va amallar formasi.

| Amal | Ta’siri | Kim |
| --- | --- | --- |
| `INITIAL` (boshlang‘ich qoldiq) | `+N`, bir marta kiritiladi | admin |
| `RECEIVED` (kirim) | `+N` | operator+ |
| `REMOVED` (chiqim) | `-N` | operator+ |
| `RETURNED` (qaytarish) | `+N` | operator+ |
| `TRANSFER` (ichki ko‘chirish) | Soni o‘zgarmaydi, joylashuv yoziladi | operator+ |
| `CORRECTION` (tuzatish) | `±N` (sabab majburiy) | operator+ |

Balans ko‘rsatkichlari:

- **Kutilgan (expected)** — tasdiqlangan qatorlar yig‘indisi.
- **Kirim/Chiqim kutilmoqda** — tasdiqlanmagan (PENDING) nomzodlar.
- **Prognoz (projected)** — `expected + pending_in − pending_out`.
- **Ro‘yxatga olingan aktivlar** — `/equipment` dagi inventar raqamlari soni.
- **Farq (discrepancy)** — `expected − aktivlar` (aktivlar ro‘yxatga olingan bo‘lsa).
- **Muammo bor** — `pending_out > 0` yoki farq noldan boshqacha.

## 9.7. Harakatlar jurnali (`/movements`)

Barcha hodisalar: sana oralig‘i, kamera, xodim, toifa, tur, holat va matnli qidiruv bo‘yicha filtrlar.
Sahifalash 40 qatordan. Har bir qatorda dalil rasmi va tafsilotlar.

## 9.8. Tekshirish (`/review`, operator+)

Har bir karta: dalil rasmi, tizim taxmini (xodim), jihoz toifasi, yo‘nalish, deteksiya ishonchi,
yuz o‘xshashligi, bog‘lanish ishonchi.

- **Tasdiqlash** — hodisa `CONFIRMED`; bog‘liq jurnal qatorlari tasdiqlanadi, balans o‘zgaradi.
  Kerak bo‘lsa xodim/jihoz/toifani shu yerda aniqlashtirish mumkin.
- **Rad etish** — sabab **majburiy**; qatorlar rad etilgan bo‘ladi, balansga ta’sir qilmaydi.

### Rad etishda sabab majburiy

“Izoh / sabab” yorlig‘ida **qizil `*`** turadi. Sabab yozilmasa:

1. **Brauzer to‘xtatadi** — forma yuborilmaydi, maydon ostida qizil yozuv chiqadi:
   “Rad etish uchun sabab kiritilishi shart — maydonni to‘ldiring.”
2. **Server ham tekshiradi** — `POST /api/events/<id>/review` bo‘sh sabab bilan kelsa, hodisa
   `PENDING` holida qoladi va xabar beriladi (brauzer himoyasini chetlab o‘tib bo‘lmaydi).

Sabab `notes` ga va `audit_logs` ga yoziladi: “kim, qachon, nimani nega rad etdi” savoli yopiq.
Maydon 500 belgigacha; yozishni boshlaganingizda qizil yozuv o‘zi yo‘qoladi.

### Dalil va hodisani o‘chirish (faqat admin)

Kartada (karta boshining o‘ng tomonida) admin uchun ikki tugma bor:

| Tugma | Nima bo‘ladi |
| --- | --- |
| **Dalilni o‘chirish** | JPEG fayl diskdan o‘chadi, hodisa jadvalda qoladi (`evidence_path` bo‘shaydi) |
| **Hodisani o‘chirish** | Hodisa + dalil butunlay o‘chadi |

Ikki himoya: **tasdiqlangan** (`CONFIRMED`) hodisa o‘chirilmaydi va **inventar jurnaliga bog‘langan**
(`EQUIPMENT_TAKEN_OUT` va h.k.) hodisa o‘chirilmaydi — append-only qoidasi buzilmaydi.
Operator va kuzatuvchi bu tugmalarni **ko‘rmaydi** (403).

Pastda **“Inventar farqlari”** jadvali: kutilgan, kutilayotgan chiqim, farq.

> **Sahifa bo‘sh bo‘lsa:** (1) kamera uchun chiziq belgilanmagan bo‘lishi mumkin — eng ko‘p uchraydigan sabab;
> (2) kamera OFFLINE; (3) barcha hodisalar allaqachon ko‘rib chiqilgan. Chiziqni `/cameras` da to‘ldiring.

## 9.9. Hisobotlar (`/reports`)

Davr bo‘yicha jamlanmalar, xodim/kamera/toifa kesimida; **CSV** (Excel uchun BOM bilan) va JSON API.
CSV fayl Excel’da to‘g‘ri ochilishi uchun UTF-8 BOM bilan yoziladi.

## 9.10. Sozlamalar (`/settings`, faqat admin)

- **Foydalanuvchilar:** qo‘shish, rol berish, bloklash, parol almashtirish.
- **Galereyani yangilash** — shablonlar xotiraga qayta yuklanadi (odatda avtomatik: 20 s).
- **YOLO modelini qayta yuklash** — `data/models/equipment_best.pt` almashtirilgandan keyin.
- **Deteksiya qoidalarini yangilash** — toifalar (`yolo_labels`) o‘zgarganda.
- **Google Sheets:** yoqish/o‘chirish, ID, sinxronlash (15-bo‘lim).
- Oq ro‘yxatdagi `.env` qiymatlarini tahrirlash (fayl atomik yoziladi, kameralar qayta ishga tushadi).

## 9.11. Yuklab olish (`/downloads`)

Olti turdagi ma’lumotni **Excel (.xlsx)** yoki **CSV** qilib yuklab olish sahifasi
(batafsil: 16-bo‘lim). Havola chap menyudagi **“Yuklab olish”** bandi.

## 9.12. Google Sheets (`/sheets`, faqat admin)

Kalit yuklash, jadvalni ulash va sinovdan o‘tkazish uchun uch qadamli ustaxona
(batafsil: 15-bo‘lim). Hisobot kartasi `/reports#sheets` da qoladi.

## 9.13. Ma’lumotlarni tozalash (`/data`, faqat admin)

Keraksiz rasmlar va eski “shovqin” hodisalarini o‘chirish sahifasi. Chap menyuda
**“Ma’lumotlarni tozalash”** (Boshqaruv guruhi). Operator uchun bu sahifa **403**.

**Yuqoridagi ko‘rsatkichlar:** hodisalar soni holat bo‘yicha, dalil fayllari soni va hajmi,
“egasiz” (orphan) fayllar, baza hajmi, dalil saqlash muddati.

| Blok | Nima qiladi | Tasdiqlash |
| --- | --- | --- |
| **Eski dalillarni tozalash** | `N` kundan eski JPEG fayllarni o‘chiradi; hodisa yozuvi qoladi | `OCHIRISH` so‘zi |
| **Egasiz fayllar** | Bazada yo‘q hodisaga tegishli JPEG’larni o‘chiradi | `OCHIRISH` so‘zi |
| **Hodisalarni tozalash** | Guruh (kamera / shaxs / jihoz / qo‘lga olish) + holat + yosh bo‘yicha ommaviy o‘chirish | `OCHIRISH` so‘zi + sabab |

Qoidalar (hammasi **audit log** ga yoziladi):

- **Tasdiqlangan** (`CONFIRMED`) hodisa hech qachon ommaviy o‘chirilmaydi.
- **Inventar jurnaliga bog‘langan** hodisa o‘chirilmaydi — `inventory_transactions` hech qachon
diskdan yo‘qolmaydi. Kerak bo‘lsa avval hodisani rad eting yoki jurnalda `CORRECTION` yozing.
- Kamera uzilish/tiklanish yozuvlari “kamera” guruhida; “qo‘lga olish” guruhi —
`EQUIPMENT_PICKED_UP` / `EQUIPMENT_PUT_DOWN`.
- Tasdiqlash so‘zi xato yozilsa **hech narsa o‘chmaydi** va nechta qator qoldirilgani ko‘rsatiladi.
- Bitta hodisani o‘chirish uchun `/review` kartasidagi tugmalardan foydalaning (9.8).

## 9.14. Namuna rasmlar (`/training`, faqat admin)

“Model mausni telefon deb yozadi” degan shikoyatning **yakuniy yechimi** shu sahifa. Bu yerda
har bir jihoz toifasi uchun **haqiqiy rasmlar** yig‘iladi va keyin model shular bilan qayta o‘qitiladi.
Chap menyuda **“Namuna rasmlar”** (Boshqaruv guruhi, ◫ belgisi). Operator uchun sahifa **403**.

**Yuqoridagi ko‘rsatkichlar:** jami namunalar soni, nechta toifa tayyor (kamida 10 ta rasm),
har bir toifa uchun maqsad (15 ta) va namunalar saqlanadigan papka (`data\samples`).

| Blok | Nima qiladi |
| --- | --- |
| **Toifalar jadvali** | Har toifa: rasm soni, `tayyor` / `N ta kerak` / `bo‘sh` belgisi va foizi. Toifa nomini bosib tanlang |
| **Rasm(lar) qo‘shish** | Bir vaqtda bir nechta fayl (JPG/PNG/WEBP, har biri 8 MB gacha). Fayllar JPEG’ga keltirilib saqlanadi |
| **Kameradan kadr olish** | Kamera tanlanadi va joriy kadr bitta namuna bo‘lib saqlanadi |
| **Yig‘ilgan namunalar** | Kichik rasmlar (thumbnail), har birining o‘lchami va vaqti, har biri uchun **O‘chirish** |
| **ZIP** | Tanlangan toifani (yoki hammasini) arxiv qilib yuklab olish |

**Qanday rasm yig‘ish kerak (maus misolida):**

1. Stol ustidagi maus — 3–4 ta, turli burchakdan.
2. **Qo‘ldagi maus** (aynan shikoyat qilingan holat) — 4–5 ta.
3. Klaviatura yonida, monitor ostida, kabel bilan — 3–4 ta.
4. Kamera masofasidan (yaqin va uzoq) — 2–3 ta.

Jami **10–15 ta**; kamida 10 ta bo‘lsa toifa `tayyor` bo‘ladi. Monitor, klaviatura, telefon va boshqa
chalkashadigan toifalar uchun ham xuddi shunday qiling — ayniqsa **adashitirilgan holatlar**
(masalan qo‘ldagi maus, quloq yonidagi telefon) albatta kirsin.

Qoidalar:

- Bir xil rasm ikki marta saqlanmaydi (nusxa aniqlanadi va “allaqachon to‘plamda bor” deyiladi).
- Bitta toifada ko‘pi bilan 80 ta rasm saqlanadi (tasodifiy yuklashdan himoya).
- Har bir amal (`samples.add`, `samples.capture`, `samples.delete`) **audit log** ga yoziladi.
- Namunalar **o‘z-o‘zidan modelni o‘zgartirmaydi**: ular faqat to‘plam. Modelni yangilash uchun
  11.2-banddagi qadamlar (import → belgilash → `--check` → `--run`) bajariladi.

---

# 10. Xodim va texniklarni rasm bilan o‘qitish

Yuz tanish sifati **rasmlarga** bog‘liq. Qoida oddiy: **shabloni ko‘p va xilma-xil bo‘lsa, o‘qish yaxshi.**

## 10.1. Usul A — paneldan, bitta xodim (eng oson)

1. `/employees` → xodim kartasi.
2. **Yangi rasmlar (2–5 ta)**: old tomondan, normal yorug‘likda, yuz to‘siqsiz (niqob/ko‘zoynak yo‘q),
   yonidan 30–45° burilgan bittasi ham bo‘lsin.
3. Tugma: **Yuklash va shablon yaratish**.
4. Natija: “N ta rasm qabul qilindi, M tasi qo‘shilmadi” + sabab. Pastda “Yuz shablonlari” ro‘yxatida paydo bo‘ladi.

## 10.2. Usul B — kameradan rasm olish (aniqlik uchun eng foydali)

Muammo: skanerlangan pasport rasmi bilan kameradagi kadr boshqacha (linza, burchak, yorug‘lik, siqilish).
Shuning uchun **kameraning o‘zidan** olingan rasm eng yaxshi ishlaydi.

1. Xodim kartasida **“Kameradan rasm olish”** blokini toping.
2. U yerda kamera kadri jonli ko‘rinadi (har 4 soniyada yangilanadi).
3. Xodim kameraga qarab, yorug‘lik normal bo‘lganda tugmani bosing: **“Kameradan rasm olish (CAM)”**.
4. Tizim kadrdan eng katta yuzni topadi, atrofini 45 % qo‘shimcha bilan kesadi va shablon yaratadi.
5. Xabarlar: “Kameradan rasm olindi va shablon yaratildi”. Yuz topilmasa:
   “Kadrda yuz topilmadi. Xodim kameraga qarab tursin.”; kamera o‘chiq bo‘lsa:
   “Kameradan kadr olinmadi. Kamera yoqilganini tekshiring.”

**Tavsiya:** 2–3 marta, turli holatda (to‘g‘ri qarab, biroz burilib, kunduzgi yorug‘likda). Har bosishda
1 ta yangi shablon qo‘shiladi; 15 talik chegara amal qiladi.

## 10.3. Usul C — papka bilan ommaviy o‘qitish (CLI)

Papka tuzilmasi (`<Ism Familiya>` papkalari, ichida rasmlar):

```text
C:\rasmlar\texniklar\
├── Otabek Bakhodirbekov\
│   ├── 1.jpg
│   ├── 2.jpg
│   └── 3.jpg
└── Diyorbek Abduhakimov\
    ├── 1.jpg
    └── 2.jpg
```

```bat
REM 1) Avval ko‘rish (bazaga yozmaydi)
.venv\Scripts\python.exe scripts\enroll_employees.py --folder C:\rasmlar\texniklar --dry-run

REM 2) Xodimlar bir-biriga juda o‘xshamasligini tekshirish
.venv\Scripts\python.exe scripts\enroll_employees.py --folder C:\rasmlar\texniklar --check

REM 3) Haqiqiy yozish (bo‘lim nomi bilan)
.venv\Scripts\python.exe scripts\enroll_employees.py --folder C:\rasmlar\texniklar ^
    --department Texniklar
```

Foydali kalitlar: `--given-first` (papka nomi “Ism Familiya” tartibida), `--dry-run`, `--check`.

Skript: mavjud xodimni **ism bo‘yicha topadi** (takror yaratmaydi), rasmni SHA-256 bo‘yicha
takrorlarni tashlaydi, har bir rasm uchun alohida shablon yozadi va oxirida jadval chiqaradi.
Yozuvdan keyin galereya 20 soniya ichida o‘zi yangilanadi (qo‘lda “Galereyani yangilash” ham mumkin).

## 10.4. Bitta xodimni CLI bilan qo‘shish

```bat
.venv\Scripts\python.exe scripts\register_employee.py --list
.venv\Scripts\python.exe scripts\register_employee.py ^
  --first-name Diyorbek --last-name Abduhakimov --department DXM ^
  --employee-number 1024 --photos "C:\rasmlar\diyor1.jpg" "C:\rasmlar\diyor2.jpg"

REM mavjud xodimga rasm qo‘shish
.venv\Scripts\python.exe scripts\register_employee.py --add-photos EMP002 ^
    --photos "C:\rasmlar\yangi.jpg"
```

## 10.5. Sifatni tekshirish (o‘zingizni sinash)

1. `/live` ni oching, xodim kamera oldidan o‘tsin.
2. `logs/app.log` da shunday qator bo‘lishi kerak:

```text
Trek 59 xodim bilan bog'landi: Otabek Bakhodirbekov (0.701) markaz=...
```

3. Raqamlar: `> 0.60` — yaxshi, `0.45–0.60` — qabul qilinadi, `< 0.45` — shablon qo‘shish kerak.
4. Ism hech chiqmasa: `FACE_SIMILARITY_THRESHOLD` ni `0.42` ga tushiring yoki kameradan rasm qo‘shing.

---

# 11. Aniqlikni oshirish: amaliy qoidalar

| № | Qoida | Sababi |
| --- | --- | --- |
| 1 | Har bir xodimga **3–5 ta** shablon, turli burchakdan | Bitta rasm bitta holatni “biladi” |
| 2 | Yuz **kadrning 10 % idan kichik** bo‘lmasin | Kichik yuzda deteksiya ishonchsiz |
| 3 | Kamera **yuz sathida** (1.4–1.7 m), orqadan yorug‘lik bo‘lmasin | Qarshi yorug‘lik yuzni qoraytiradi |
| 4 | Kadrda **bir vaqtda 1–3 odam** | Ko‘p odamda yuz kichrayadi va bir-biriga o‘xshaydi |
| 5 | Har kuni **kameradan rasm** qo‘shib borish | Linza/burchak/oyna nuri o‘zgaradi |
| 6 | Chiziq **to‘g‘ri** qo‘yilgan bo‘lsin | Chiziqsiz kirim/chiqim hodisasi yo‘q |
| 7 | Chegaralarni **o‘lchab** o‘zgartirish | `0.45` universal emas: 0.40–0.55 oralig‘ida sinang |
| 8 | CPU sekin bo‘lsa `PROCESSING_FPS` ni tushiring | Kadr tashlab yuborilsa trek uzilib qoladi |
| 9 | Jihoz uchun **custom YOLO** | `yolov8s` printer/ups/ip telefon/lan kabel’ni bilmaydi |
| 10 | Bir xil kiyimdagi odamlarni aralashtirmaslik | `FACE_MATCH_MARGIN` “taxmin qilmaslik” uchun |
| 11 | Toifalarning **minimal ishonch** i mahsulot turiga mos bo‘lsin — mayda jihoz 0.70–0.75 (11.1) | Past chegara (0.25) “mausni telefon” deb yozadi |
| 12 | `YOLO_DEDUPE_IOU` ni 0.6 da qoldirish | Bitta obyekt ikki sinf bo‘lib jurnalga ikki marta yozilmasin |
| 13 | Obyekt **nisbati va maydoni** tekshirilsin (11.1) | Telefon — tik to‘rtburchak; maus — yassi va keng |
| 14 | Maus/klaviatura ustidagi “telefon” belgisi rad etilsin (11.1) | YOLO stol jihozini tez-tez telefon deb chalkashtiradi |
| 15 | Chalkashadigan toifalar uchun **10–15 ta namuna rasm** yig‘ilsin (9.14) | Chegaralar xatoni kamaytiradi, **model** esa faqat ko‘rgan narsasini taniydi |

**Chegaralarni qachon o‘zgartirish:**

| Holat | Nima qilish |
| --- | --- |
| Ism chiqmayapti (o‘z xodimi) | Shablon qo‘shish → `FACE_SIMILARITY_THRESHOLD` 0.42 → `FACE_RECOGNITION_INTERVAL` 5 |
| Begona odam xodim deb tanilyapti | Chegarani 0.50–0.55 ga **oshirish**, `FACE_MATCH_MARGIN` 0.08 |
| “Kirdi” o‘rniga “chiqdi” yozilyapti | `/cameras` → **Chiziqni teskari** |
| Hech qanday hodisa yo‘q | Chiziq borligini tekshirish (9.4), kamera `ONLINE` |
| Jihoz aniqlanmayapti | Toifaning `min_confidence` i 11.1 dagi **poldan yuqori** bo‘lmasin; keyin modelni tekshirish |
| “Maus” telefon deb yozilyapti (yoki aksi) | 11.1 dagi shakl qoidasi va chalkashuv yechish yoqilganini tekshirish; log’da chegara ko‘tarilganini ko‘rish |
| Bitta obyekt ikki marta jurnalga tushyapti | `YOLO_DEDUPE_IOU` = 0.6 (kichikroq: 0.5), toifalarda bir xil sinf nomi qolmaganini tekshirish |
| Toifa “sakrab” o‘zgaryapti (tv ↔ monitor) | Bu normal: trek bo‘yicha ko‘pchilik sinf olinadi; aniq model bilan barqarorlashadi |

**Nega yangi toifalar darhol ko‘rinmaydi.** Tizim toifani bilsa ham, uni faqat **model**
tanidi. `yolov8s.pt` (COCO) da `ip_phone`, `landline_phone`, `lan_tester`, `lan_cable`,
`camera`, `mini_camera`, `projector`, `printer`, `ups`, `server` sinflari **yo‘q** — ular
faqat o‘qitilgan `best.pt` bilan ishlaydi (`docs/yolo_training.md`, 27 sinf). COCO rejimida
ishlaydiganlar: `person`, `laptop`, `tv` (monitor ham), `cell phone` (smartfon), `keyboard`,
`mouse`.

## 11.1. Ishonch polining va shakl qoidalari (2026-10-29 dan)

“Maus telefon deb yozilyapti” va “quloq yonidagi qo‘l smartfon deb tanilyapti” shikoyatining
asosiy sababi — **juda past chegara**: `yolov8s` stol usti mayda narsalarni 0.30–0.45 ishonch bilan
belgilaydi, eski chegaralar esa 0.25–0.45 edi. Endi har bir toifa uchun qat’iy pol bor:

| Toifalar | Eng past ishonch |
| --- | --- |
| `smartphone`, `tablet`, `ip_phone`, `landline_phone`, `mini_camera` | **0.75** |
| `camera`, `mouse`, `keyboard`, `flash_drive`, `external_hdd`, `lan_tester`, `lan_cable`, `other_equipment` | **0.70** |
| qolgan jihoz toifalari (noutbuk, monitor, printer, tv…) | **0.60** |
| `person` (xodimni kuzatish uchun) | 0.45 — o‘zgarmaydi |

- Pol **har ishga tushishda** `enforce_confidence_floors()` bilan bazaga yoziladi: chegara faqat
  **ko‘tariladi**, hech qachon tushirilmaydi. Log’da `Aniqlik chegarasi ko‘tarildi: ...` qatori chiqadi.
- `/settings` da toifaga **yuqoriroq** chegara berish mumkin; pastroq berilsa, keyingi ishga tushishda
  yana polga qaytadi (ataylab shunday — tasodifan “hammasi o‘tadi” holatiga tushib qolmaslik uchun).

**Shakl qoidasi (geometriya).** Har bir mayda jihoz uchun ruxsat etilgan **nisbat** (kenglik/balandlik)
va **kadrdagi maydon ulushi** belgilangan:

| Toifa | Nisbat (k/h) | Kadr maydonidan |
| --- | --- | --- |
| `smartphone` | 0.28–1.25 | 0.12 %–10 % |
| `tablet` | 0.45–1.90 | 0.20 %–15 % |
| `mouse` | 0.45–2.60 | 0.08 %–**20 %** |
| `keyboard` | 1.60–6.00 | 0.20 %–20 % |
| `lan_cable` | 0.05–8.00 | 0.08 %–30 % |

Qoidaga to‘g‘ri kelmaydigan quti **umuman qabul qilinmaydi**: juda yassi va keng dog‘ (stol cheti,
skarlet, devor chizig‘i) telefon bo‘lib qolmaydi; juda kichik nuqta ham tashlanadi.

> **Maus chegarasi 2026-10-29 da yumshatildi:** qo‘ldagi maus kameraga yaqin tutilganda
> kadrning 15–18 % ini egallaydi, eski 6 % chegara esa **to‘g‘ri** maus qutisini ham tashlab
> yuborardi — natijada telefonga o‘xshash boshqa quti g‘olib bo‘lib qolardi. Endi `mouse`
> uchun yuqori chegara **20 %**, telefon uchun esa 10 % (yaqin plandagi maus telefon
> hisoblanmaydi, teskarisi ham).
>
> **Nisbat ham yumshatildi (0.80 → 0.45):** maus **tik** tutilganda (barmoqlar orasida)
> kenglik/balandlik nisbati 0.65 bo‘ladi — eski 0.80 pastki chegara bu **to‘g‘ri** maus
> qutisini rad etardi. Natijada “telefon ↔ maus” chalkashuvini yechish ham ishlamay
> qolardi: telefon belgisini bekor qiladigan maus qutisi o‘zi yo‘q bo‘lib qolardi.
> Endi maus uchun nisbat 0.45–2.60.
>
> **Halol cheklov:** bu qoida **modelni o‘zgartirmaydi**. COCO modeli qo‘lda tik tutilgan
> oq mausni baribir `cell phone` (0.77–0.91 ishonch) deb topadi: nisbat 0.65, maydon 7 % —
> ya’ni telefon qoidasiga **to‘g‘ri** keladi. Maus va telefon bir kadrda geometriya bilan
> ajratilmaydi; bunday holat faqat o‘qitilgan model bilan tuzatiladi (11.2).

**Chalkashuvni yechish.** Bir joyda `smartphone` va `mouse`/`keyboard` qutilari ustma-ust tushsa
(IoU ≥ 0.35), **telefon belgisi tashlab yuboriladi** — stol jihozi telefonga aylanmaydi. Shundan keyin
odatdagi `dedupe_overlaps` (IoU 0.6) bitta obyektni ikki marta yozishdan saqlaydi.

> “Quloq yonidagi qo‘l” holati: model qo‘lni emas, **telefonni** ko‘rishi kerak. Qo‘l telefon
> o‘lchamida ko‘rinsa va ishonch 0.75 dan past bo‘lsa, quti rad etiladi. Baribir yozilib qolsa —
> `/review` da rad eting (sabab majburiy) yoki admin sifatida o‘chiring (9.13).

## 11.2. Namunalar bilan modelni qayta o‘qitish (maus ≠ telefon)

Chegara va shakl qoidalari (11.1) **yolg‘on** hodisani kamaytiradi, lekin modelning o‘zini
o‘zgartirmaydi: `yolov8s` hali ham mausni “cell phone” deb ko‘rishi mumkin. Model faqat
**o‘z ko‘zi bilan ko‘rgan** narsani taniydi. Shuning uchun zanjir quyidagicha:

1. **Yig‘ish** — `/training` da har bir chalkashadigan toifa uchun 10–15 ta rasm (9.14).
   Eng qimmatlisi — **kameraning o‘zidan** olingan kadrlar: internetdan olingan oq fonli
   suratlar modelga “maus nima ekanini” o‘rgatadi, lekin sizning xonangiz, yorug‘ligingiz
   va qo‘lda tutilgan holatni ko‘rsatmaydi. Shuning uchun 10 tadan kamida 6–7 tasi kameradan bo‘lsin.
2. **Import** — namunalar dataset ichiga ko‘chiriladi (rasm o‘chirilmaydi, nusxa olinadi):

   ```bat
   .venv\Scripts\python.exe scripts\train_yolo.py --init dataset
   .venv\Scripts\python.exe scripts\train_yolo.py --import-samples dataset
   ```

   Har bir sinf uchun nechta rasm qo‘shilgani chiqadi. Fayl nomi `<sinf>__<nomi>.jpg` bo‘ladi.

   **2a. Ixtiyoriy — ochiq to‘plamdan qo‘shimcha kadrlar.** O‘z kamerangizdan 300–500 kadr
   yig‘ilguncha, ochiq `YOLO-DeskDataset` (1 646 kadr, 80 sinf) dan foydalanish mumkin. Skript
   faqat loyiha toifasiga **aynan mos** 8 sinfni oladi: `laptop`, `mouse`, `keyboard`,
   `monitor`, `smartphone`, `tablet`, `printer`, `speaker`.

   ```bat
   .venv\Scripts\python.exe scripts\import_public_dataset.py --dry-run
   .venv\Scripts\python.exe scripts\import_public_dataset.py --max-per-class 60
   .venv\Scripts\python.exe scripts\import_public_dataset.py --split val --max-per-class 12
   ```

   Skript `data.yaml` ni, mavjud kadrlarni va `data\models` ni o‘zgartirmaydi (faqat yangi fayl
   qo‘shadi). Ikkita cheklov: **`person` sinfi bu to‘plamda yo‘q** (shu sababli kamera kadrlari
   baribir kerak) va kadrdagi begona buyumlar (krujka, qalam) labeldan chiqadi.

   Nega kerak: 76 ta sinov kadrida COCO modeli `monitor`, `tablet`, `printer`, `speaker` ni
   **0 %** topadi, `smartphone` da to‘g‘ri topish 31 % (P=0.45). Bu to‘rt toifa loyihada umuman
   ishlamaydi — ularni faqat o‘qitish o‘rgatadi.
3. **Qutilarni avtomatik chizish** (qo‘lda ishni 10 barobar kamaytiradi):

   ```bat
   .venv\Scripts\python.exe scripts\autolabel_dataset.py --dataset dataset
   ```

   Joriy model qutilarni o‘zi topadi, ularni `.txt` ga yozadi va
   `dataset\preview\train\` ichiga **qutilari chizilgan nusxa** yaratadi. Rasm nomidagi toifa
   (`mouse__…`) hisobga olinadi: model mausni “cell phone” deb topgan bo‘lsa, sinf `mouse` ga
   to‘g‘rilanadi. Shakl/o‘lcham qoidasiga to‘g‘ri kelmagan qutilar tashlanadi (11.1).
   Keyin preview rasmlarni ochib tekshiring; qolgan bo‘sh kadrlarni LabelImg/CVAT bilan belgilang.
4. **Belgilash (qo‘lda)** — LabelImg yoki CVAT bilan qutilarni tuzatish va bo‘sh kadrlarni
   belgilash (`mouse`, `smartphone`, …). Belgilanmagan rasm o‘qitishda **ishtirok etmaydi**.
5. **Tekshirish** — `scripts\train_yolo.py --check dataset\data.yaml`. Talab: **100+** `train`
   va **50+** `val` belgilangan kadr; kam bo‘lsa `--check` ro‘yxat qilib beradi.
   `val` bo‘sh bo‘lsa, bir qismini shu buyruq bilan ajrating (har sinfdan kamida bittasi
   `val` ga tushadi, belgilangan kadrlar ustuvor):

   ```bat
   .venv\Scripts\python.exe scripts\train_yolo.py --split-val 0.2
   ```
6. **O‘qitish** — `--run --epochs 40 --device cpu` (GPU bo‘lsa `--device 0`).
7. **Almashtirish** — yangi `best.pt` ni `data\models\equipment_best.pt` ga qo‘yib,
   `/settings` → **YOLO modelini qayta yuklash**.

> **Himoya:** agar `labels/train` da **100 tadan kam** belgilangan kadr bo‘lsa, `--run` dan keyin
> model prodakshnga **avtomatik qo‘yilmaydi** (log: “Prodakshn modeli almashtirilmadi”). Sabab:
> 20–30 kadr bilan o‘qitilgan model boshqa toifalarni yomonlashtirishi mumkin. Baribir almashtirish
> kerak bo‘lsa `--deploy` qo‘shing.
>
> Ikkinchi himoya: datasetda **`person` sinfi umuman bo‘lmasa** model ham avtomatik
> almashtirilmaydi. Shu sinfsiz model xodimlarni ko‘rmaydi — yuzni tanish, xodimga bog‘lash va
> inventar hodisalari ishlamay qoladi. Faqat ochiq to‘plam bilan o‘qitilgan model shu holatga
> tushadi; kamera kadrlarini qo‘shsangiz (`--import-samples`) muammo yo‘qoladi.

**Halol eslatma:** panelda rasm yuklashning o‘zi modelni o‘zgartirmaydi. 2–6 qadamlar bajarilmaguncha
aniqlik avvalgi darajada qoladi. Namunalar to‘plami esa keyingi o‘qitish uchun asos bo‘lib turadi —
shuning uchun ularni `/data` tozalashdan ajratib qo‘ying (u yerda faqat dalil rasmlari o‘chadi).

---

# 12. Inventar qoidalari (append-only jurnal)

Har bir harakat `inventory_transactions` jadvaliga **yangi qator** sifatida yoziladi; qatorlar
tahrirlanmaydi va o‘chirilmaydi. Joriy qoldiq har doim yig‘indi sifatida hisoblanadi — shuning uchun
“kim, qachon, nima uchun” degan savolga har doim javob bor.

Amallar jadvali 9.6 da. Qo‘shimcha qoidalar:

- **Rad etilgan** hodisa jurnalda audit uchun qoladi, lekin na balansga, na “kutilmoqda” ko‘rsatkichiga
  qo‘shiladi.
- `CORRECTION` da **sabab majburiy** — aks holda forma xato beradi.
- `INITIAL` ni keyin o‘zgartirish uchun yangi `CORRECTION` qatori yoziladi (eski qator tegilmaydi).
- Kamera hodisasi faqat **nomzod** yaratadi: `+1`/`-1`, `PENDING`. Tasdiqlanmaguncha qoldiq o‘zgarmaydi.

---

# 13. Hodisalar, dublikatlar va dalillar

**Turlar:** `PERSON_ENTERED/EXITED`, `UNKNOWN_PERSON_ENTERED/EXITED`,
`UNKNOWN_PERSON_DETECTED`, `EQUIPMENT_PICKED_UP`, `EQUIPMENT_PUT_DOWN`,
`EQUIPMENT_BROUGHT_IN`, `EQUIPMENT_TAKEN_OUT`, `EQUIPMENT_RETURNED`,
`INVENTORY_DISCREPANCY`, `CAMERA_DISCONNECTED`, `CAMERA_RECONNECTED`.

**Holat:** `AUTO` (ishonchli, xodim aniq tanildi), `PENDING` (tekshirish kerak),
`CONFIRMED`, `REJECTED`.

## 13.1. Jihozni qo‘lga olish zanjiri

Bu zanjir **chiziqqa bog‘liq emas** — kamera `ONLINE` bo‘lishi bilan ishlaydi.

| Qadam | Qachon yoziladi | Natija |
| --- | --- | --- |
| Olindi | jihoz qutisi markazi odam tanasi ichida (35 % kengaytirilgan) ketma-ket **3 kadr** | `EQUIPMENT_PICKED_UP`; jihoz shu odamga biriktiriladi |
| Ushlab turish | qisqa uzilishlar (bir necha kadr) | biriktirish saqlanadi, xodim yuzi qayta tanilsa yangilanadi |
| Qo‘yildi | jihoz **6 s** ko‘rinadi, lekin tana bilan qoplanmaydi | `EQUIPMENT_PUT_DOWN`; izohda qancha ushlangani |
| Kadrdan chiqdi | jihoz treki butunlay yo‘qoldi | put-down yozilmaydi — chiziq kesilganda kirim/chiqim chiqadi |
| Kadrda ko‘rinmadi | jihoz tana ortida/sumkada, trek yo‘qolgan | biriktirish **45 s** eslab qolinadi; chiziq kesilganda hodisa baribir yoziladi (izoh: “kadrda ko‘rinmadi”, `Tekshirish kerak`) |

- Tasodifiy yonma-yon turish (javon, stol, stend) **biriktirilmaydi**: faqat tana bilan
  qoplanish hisobga olinadi (yaqinlikning o‘zi yetarli emas).
- Chiziq kesilganda ushlab turilgan jihoz **egasi bilan birga** yoziladi: xodim,
  `held_since`, `held_seconds`, dalil kadri va aniq vaqt.
- `EQUIPMENT_PICKED_UP` / `EQUIPMENT_PUT_DOWN` — **kuzatuv** hodisalari. Ular inventar
  jurnaliga nomzod yaratmaydi, ya’ni qoldiqni o‘zgartirmaydi.
- Dashboard’dagi **“Hozir qo‘lda”** kartasi ayni damda kim nimani ushlab turganini
  ko‘rsatadi (Jihoz / Xodim / Kamera / Olindi / Qo‘lda s).

## 13.2. “Hodisalar (birinchi 200)” jadvali

`/reports` dagi jadval ustunlari: **Vaqt, Hodisa, Kamera, Xodim, Jihoz, Yo‘nalish,
Aniqlik, Holat, Dalil** (aynan `/movements` bilan bir xil).

- Karta faqat **biznes hodisalarini** ko‘rsatadi; kamera uzilish/tiklanish yozuvlari
  jadvalni to‘ldirmaydi. To‘liq tarix: `/movements?type=CAMERA_DISCONNECTED`.
- Jihoz ustunida `qo‘lda N s` yozuvi — jihoz shu odamda qancha vaqt turgani.
- Kamera `ONLINE` bo‘lishi bilan yangi qatorlar o‘zi paydo bo‘ladi (sahifani yangilang).

**Ikki qatlamli dublikat himoyasi:**

1. **Dedupe kaliti** (`mv:` / `pp:` / `pos:` / `sys:`) — bir xil trek/kamera/kategoriya
   uchun takror yozilmaydi. `pos:` kaliti jihoz + odam juftini saqlaydi: bir sekundda
   ikki xil jihoz ko‘tarilsa, ular bir-birini bekor qilmaydi.
2. **Cooldown** (`EVENT_COOLDOWN_SECONDS`, 20 s) — kalit vaqt oynasi bilan hisoblanadi.

**Dalillar:** `EVIDENCE_ENABLED=true` bo‘lsa hodisa kadri `data/evidence/YYYY-MM-DD/` ga JPEG
(`EVIDENCE_JPEG_QUALITY`) qilib saqlanadi, `EVIDENCE_RETENTION_DAYS` (30 kun) dan keyin tozalanadi.
Dalil faqat operator va adminga ko‘rinadi (`/api/events/<id>/evidence`).

**Bog‘lanish ballari (0..1):** 0.50 × olib yurish + 0.25 × yaqinlik + 0.15 × vaqt + 0.10 × yo‘nalish.
`STRONG` (≥ 0.65), `WEAK`, `AMBIGUOUS` (1- va 2-nomzod farqi kichik), `NONE`.
`WEAK`/`AMBIGUOUS` bo‘lsa hodisa izohida “Tekshirish kerak” yoziladi va `PENDING` bo‘lib qoladi —
aynan shu yozuvlar `/review` da ko‘rinadi.

## 13.3. O‘chirish qoidalari (dalil, hodisa, ma’lumot)

O‘chirish — **faqat admin** huquqi. Uch daraja bor:

| Daraja | Qayerda | Nima o‘chadi | Himoya |
| --- | --- | --- | --- |
| Dalil (JPEG) | `/review` kartasi | faqat rasm fayli | hodisa va jurnal qoladi |
| Bitta hodisa | `/review` kartasi | hodisa + rasmi | `CONFIRMED` va jurnalga bog‘langan qator himoyalangan |
| Ommaviy | `/data` (9.13) | guruh/holat/yosh bo‘yicha qatorlar va rasmlar | `OCHIRISH` so‘zi + sabab, `CONFIRMED` o‘tmaydi |

**O‘zgarmas qoida:** `inventory_transactions` jadvali **hech qachon** o‘chirilmaydi. Shuning uchun
tizim “bu jihoz qayerda?” degan savolga har qanday tozalashdan keyin ham javob bera oladi.
Biror hodisani o‘chirish kerak bo‘lsa va u jurnalga bog‘langan bo‘lsa:

1. Avval uni `/review` da **rad eting** (sabab majburiy) — balans o‘zgarmaydi.
2. Yoki to‘g‘ri qoldiq uchun yangi `CORRECTION` qatori yozing (9.6).

Har bir o‘chirish `audit_logs` ga yoziladi: `event.delete`, `event.evidence_delete`,
`data.purge_events`, `data.purge_evidence`. Ya’ni “kim o‘chirdi” savoli ham yopiq.

---

# 14. Rollar, xavfsizlik va audit

| Rol | Huquqlar |
| --- | --- |
| `admin` | Hamma narsa: kameralar, xodimlar va shablonlar, toifalar, foydalanuvchilar, `INITIAL`, sozlamalar, modelni qayta yuklash, **dalil/hodisalarni o‘chirish, `/data` tozalash va `/training` namunalari** |
| `operator` | Tekshirish (tasdiqlash/rad etish, bog‘lash), inventar amallari (INITIAL’dan tashqari), dalillarni ko‘rish, Sheets sinxronlash. **O‘chirish huquqi yo‘q** |
| `viewer` | Faqat o‘qish: dashboard, jonli oqim, jurnal, inventar, hisobot, CSV |

- Har bir POST so‘rov **CSRF token** bilan himoyalangan.
- Parollar **PBKDF2** bilan xeshlanadi; sessiya cookie imzolangan (`APP_SECRET_KEY`).
- `APP_ENV=production` bo‘lsa cookie faqat HTTPS orqali yuboriladi (lokal HTTP’da login ishlamaydi).
- Barcha muhim amallar `audit_logs` ga yoziladi: `category.create`, rasm o‘chirish, xodim o‘chirish,
  inventar harakati, sozlama o‘zgarishi, model qayta yuklash, hodisa/dalil o‘chirish, `/data` tozalash
  va namuna rasm qo‘shish/o‘chirish (`samples.*`).
- **Parolni birinchi kundan almashtiring**; `Admin123!` ni hech qachon qoldirmang.
- `.env` faylini hech kimga yubormang va git’ga qo‘shmang (`.gitignore` da).

---

# 15. Google Sheets: qo‘shish va ulash (3 qadam)

Google Sheets — **hisobot oynasi**, asosiy baza emas. Har bir yozuv avval lokal bazaga tushadi, keyin
ixtiyoriy ravishda jadvalga nusxalanadi. Google yoki internet ishlamasa hech narsa yo‘qolmaydi:
hodisalar `PENDING` bo‘lib turadi va keyingi urinishda yuboriladi.

Ulash uchun brauzerda **`/sheets`** sahifasi ishlatiladi (chap menyu → Boshqaruv → **Google Sheets**).
Sahifa faqat administratorga ochiq; hisobot kartasi esa `/reports#sheets` da qoladi.

## 15.1. Nima uchun “service account” kerak

Tizim sizning Google akkauntingiz parolini so‘ramaydi. Uning o‘rniga **service account** —
kompyuter uchun maxsus Google akkaunt ishlatiladi. Siz o‘sha akkauntning email manzilini jadvalga
**Editor** qilib qo‘shasiz, tizim esa kalit (JSON) orqali kiradi.

Uchta narsa kerak: (1) JSON kalit, (2) jadval ID’si, (3) yoqilgan holat.

## 15.2. 1-qadam — Google Cloud’da service account yaratish

Buni brauzerda, bir marta bajarasiz (taxminan 5 daqiqa):

1. https://console.cloud.google.com/projectcreate — yangi loyiha yarating (masalan `face-date`).
2. https://console.cloud.google.com/apis/library/sheets.googleapis.com — **Google Sheets API** → **Enable**.
3. https://console.cloud.google.com/iam-admin/serviceaccounts — **Create service account**:
   nomi `face-date-sheets`. Rol berish shart emas → **Done**.
4. Yaratilgan account’ni oching → **Keys** → **Add key** → **Create new key** → **JSON** → **Create**.
   Brauzer `*.json` faylni yuklab oladi.

> Kalit fayl — parol bilan teng. Uni chatga, emailga yoki git’ga yubormang.

## 15.3. 2-qadam — kalitni tizimga yuklash

`/sheets` → **2-qadam** kartasi:

1. **JSON fayl** maydoniga yuklab olingan faylni tanlang (yoki JSON matnini o‘ng maydonga joylashtiring).
2. **Kalitni saqlash** tugmasini bosing.
3. Natija: fayl `secrets\google_service_account.json` bo‘lib saqlanadi (faqat tizim o‘qiydigan
   ruxsat bilan), `.env` ga `GOOGLE_SERVICE_ACCOUNT_FILE` yoziladi.
4. Karta pastida **client_email** paydo bo‘ladi va **Nusxalash** tugmasi bor — keyingi qadamda kerak.

Xato bo‘lsa sahifada aniq sabab chiqadi: “JSON o‘qilmadi”, “service account kaliti emas”,
“private_key topilmadi” va hokazo.

## 15.4. 3-qadam — jadvalni ulash

1. Google Sheets’da bo‘sh jadval yarating (`sheets.new`).
2. Jadvalda **Share** → yuqoridagi `client_email` ni qo‘ying → ruxsat **Editor** → **Send**.
3. `/sheets` → **3-qadam**: havolani (`https://docs.google.com/spreadsheets/d/.../edit`) yoki faqat
   ID’ni qo‘ying, intervalni tanlang (15–86400 s, standart 120) va **Jadvalni ulash** ni bosing.

Tizim havoladan ID’ni o‘zi ajratib oladi va `.env` ga yozadi:

```text
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SPREADSHEET_ID=<jadval ID>
GOOGLE_SERVICE_ACCOUNT_FILE=secrets/google_service_account.json
```

## 15.5. Tekshirish va varaqlarni tayyorlash

- **Sinovdan o‘tkazish** — tizim jadvalni ochib ko‘radi: muvaffaqiyatli bo‘lsa jadval nomi va varaqlar
  ro‘yxati, xato bo‘lsa sabab + maslahat chiqadi (403 → jadval bo‘lishilmagan, 404 → ID xato).
- **Varaqlarni tayyorlash** — 4 ta standart varaqni sarlavhalari bilan yaratadi (mavjudlariga tegilmaydi).
- **Hozir sinxronlash** — darhol nusxa ko‘chiradi; natija **Sinxronizatsiya tarixi** jadvalida ko‘rinadi.

| Varaq | Mazmuni | Yangilanish |
| --- | --- | --- |
| `Employees` | Xodim ID, ism, familiya, bo‘lim, holat | ID bo‘yicha upsert |
| `Equipment` | Aktiv kodi, toifa, brend, model, seriya, joylashuv, holat | ID bo‘yicha upsert |
| `MovementEvents` | Event ID, vaqt, xodim, hodisa turi, jihoz, yo‘nalish, kamera, holat, izoh | Event ID bo‘yicha takrorlanmaydi |
| `InventorySummary` | Toifa bo‘yicha qoldiq, kutilayotgan harakat, farq | Har safar to‘liq yangilanadi |

## 15.6. Avtomatik, qo‘lda va CLI

- **Avtomatik** — yoqilgan bo‘lsa, fon oqimi har `GOOGLE_SHEETS_SYNC_INTERVAL_SECONDS` da ishlaydi.
  Ustaxonadagi tugma bilan yoqish/o‘chirish mumkin.
- **Qo‘lda** — `/sheets` → **Hozir sinxronlash** yoki `/reports#sheets` dagi shu nomdagi tugma.
- **CLI** — `.venv\Scripts\python.exe scripts\sync_google_sheets.py --status`.
- **Ulanishni bitta buyruq bilan sozlash** —
  `scripts\connect_google_sheets.py --url "<havola>" --key "<kalit>.json" --prepare`:
  `secrets\` papkasini yaratadi, kalitni tekshiradi, havoladan ID ajratadi, `.env` ni
  yangilaydi va ulanishni sinaydi.
- **Kalit fayli nomi muhim emas** — Google JSON faylni tasodifiy nom bilan yuklab beradi.
  `GOOGLE_SERVICE_ACCOUNT_FILE` da ko‘rsatilgan fayl topilmasa, tizim `secrets\` ichidagi
  **istalgan** haqiqiy service-account JSON’ni avtomatik tanlaydi — faylni shunchaki
  papkaga tashlash kifoya.

## 15.7. Nosozliklar

| Alomat | Sabab | Yechim |
| --- | --- | --- |
| `403` / “permission” | Jadval `client_email` bilan bo‘lishilmagan | Jadval → Share → Editor qilib qo‘shing |
| `404` / “not found” | ID xato yoki jadval o‘chirilgan | Havolani qayta nusxalab yuboring |
| `invalid_grant` | Kalit o‘chirilgan yoki muddati tugagan | Google Cloud’dan yangi JSON yuklab, 2-qadamni takrorlang |
| “Service account kalit fayli topilmadi” | `.env` dagi `GOOGLE_SERVICE_ACCOUNT_FILE` mavjud bo‘lmagan faylga ishora qiladi | 2-qadamda kalitni qayta yuklang — yo‘l o‘zi `secrets/google_service_account.json` ga yangilanadi |
| “Jadval ID shubhali ko‘rinadi” | ID qisqa yoki faqat raqamlardan iborat (44 belgili ID emas) | Brauzer manzil satridan havolani to‘liq nusxalab, 3-qadamga yuboring |
| `FAILED` hodisalar | Tarmoq yoki vaqtincha limit | Keyingi sinxronizatsiyada o‘zi qayta yuboriladi |
| Sahifa ochilmayapti | Rol admin emas | Administrator bilan kiring |

Batafsil sabab `logs\app.log` va **Sinxronizatsiya tarixi** jadvalida yoziladi.

---

# 16. Yuklab olish: Excel va CSV

`/downloads` — ma’lumotni tashqi ish uchun faylga chiqaradigan sahifa (chap menyu → **Yuklab olish**).
Yuklab olish **hech narsani o‘zgartirmaydi**: barcha fayllar bazadan faqat o‘qib olinadi.

## 16.1. Sahifadagi tartib

1. **Davr** — boshlanish/tugash sanasi. Faqat **Inventar jurnali** va **Hodisalar** ga qo‘llanadi.
2. **Bitta faylda hammasi** — 6 varaqli bitta `.xlsx` (`/downloads/all.xlsx`).
3. **Kartalar** — har bir ma’lumot turi uchun **Excel (.xlsx)** va **CSV** tugmalari.

## 16.2. Fayllar

| Fayl | Mazmuni | Davr filtri |
| --- | --- | --- |
| `inventory` | Toifalar bo‘yicha qoldiq, kutilayotgan kirim/chiqim, prognoz, farq | yo‘q |
| `ledger` | Inventar jurnali: sana, amal, toifa, miqdor, joylashuv, holat | bor |
| `events` | Kamera hodisalari: kim, qachon, jihoz, ishonch ballari, dalil yo‘li | bor |
| `employees` | Xodim kartochkalari va shablonlar soni | yo‘q |
| `assets` | Jihozlar: aktiv kodi, brend, model, seriya, joylashuv, holat | yo‘q |
| `audit` | Audit jurnali (faqat administrator) | yo‘q |

Manzil shakli: `/downloads/<fayl>.<format>`, masalan `/downloads/events.xlsx` yoki
`/downloads/ledger.csv?start=2026-10-01&end=2026-10-31`.

## 16.3. Excel va CSV farqi

- **Excel (.xlsx)** — sarlavha qatori qotirilgan, avtofiltr yoqilgan, ustun kengliklari moslangan,
  raqamlar formatlangan. Hisobotni biriktirish uchun eng qulay.
- **CSV** — UTF-8 **BOM** bilan; Excel’da o‘zbekcha harflar (`o‘`, `g‘`, `sh`) buzilmaydi.
- Ikkala formatda ham qiymatlar mahalliy vaqtda (`Asia/Tashkent`).

## 16.4. Cheklovlar va xavfsizlik

- Bir faylda ko‘pi bilan **20 000 qator**; kattaroq tarix uchun davrni qisqartiring.
- **Dalil rasmlari faylga qo‘shilmaydi** — ustunda faqat nisbiy yo‘l ko‘rsatiladi.
- Har bir yuklab olish `audit_logs` ga yoziladi: kim, qachon, qaysi fayl (`export.download`).
- Audit faylini faqat administrator oladi; qolgan fayllar barcha rollarga ochiq.

---

# 17. CLI skriptlar

| Skript | Vazifasi | Asosiy kalitlar |
| --- | --- | --- |
| `scripts\register_employee.py` | Bitta xodim qo‘shish / ro‘yxat / rasm qo‘shish | `--list`, `--first-name`, `--last-name`, `--department`, `--employee-number`, `--code`, `--photos`, `--add-photos CODE` |
| `scripts\enroll_employees.py` | Papkadan ommaviy o‘qitish | `--folder`, `--department`, `--given-first`, `--dry-run`, `--check` |
| `scripts\test_camera.py` | Kamera/manbani sinash, kadr saqlash | `--code`, `--source`, `--seconds`, `--out`, `--save` |
| `scripts\train_yolo.py` | Dataset yaratish/o‘qitish | `--init DIR`, `--check DATA_YAML`, `--import-samples DIR`, `--samples`, `--split-val RATIO`, `--force`, `--data`, `--model`, `--epochs`, `--imgsz`, `--batch`, `--device`, `--run`, `--deploy` |
| `scripts\autolabel_dataset.py` | Datasetdagi rasmlarga oldindan quti chizish (pre-label) | `--dataset`, `--data`, `--split`, `--model`, `--conf`, `--imgsz`, `--device`, `--overwrite`, `--no-hint`, `--no-preview`, `--dry-run` |
| `scripts\sync_google_sheets.py` | Sheets holati/sinxronlash | `--status` |
| `scripts\connect_google_sheets.py` | Sheets ulanishini bitta buyruq bilan sozlash | `--url`, `--key`, `--prepare`, `--sync`, `--no-enable` |

Barcha skriptlar loyiha ildizidan ishga tushiriladi va `.env` dagi bazadan foydalanadi:

```bat
cd C:\Users\Admin\Desktop\face-date
.venv\Scripts\python.exe scripts\test_camera.py --code CAM --seconds 8
```

---

# 18. Testlar

```bat
cd C:\Users\Admin\Desktop\face-date
.venv\Scripts\python.exe -m pytest -q
```

Kutilgan natija: **276 passed** (2026-10-30 holatiga). Testlar vaqtinchalik SQLite bazada ishlaydi —
`data/inventory.db` ga tegilmaydi.

Qamrov (qisqacha): 12 sahifa, login/rollar/CSRF, inventar balanslari va PENDING→tasdiq/rad oqimi,
hodisa dedupe/cooldown, chiziq kesishish mantiqi, yuz mosligi chegarasi va marjasi, kamera uzilish/tiklanish,
Sheets upsert (soxta API), Sheets ulash ustaxonasi (kalit, ID, sinov, varaqlar), Excel/CSV eksport va
huquqlar, xodim rasmlari (dedupe, 15 chegara, nomlash), kameradan rasm olish, yangi toifa,
rad etishda sabab majburiyligi, admin o‘chirish qoidalari (`/data`), **namuna rasmlar** (`/training`:
yuklash, nusxa filtr, o‘chirish, ZIP, kameradan kadr, 10 ta chegarasi).

**Diqqat:** testlar ishlayotganda server ham ishlab tursa, SQLite’da `database is locked` chiqishi mumkin.
Testlarni server to‘xtatilganda yoki alohida baza bilan ishlating.

---

# 19. Nosozliklarni bartaraf etish

## 19.1. Tez jadval

| Alomat | Sabab | Yechim |
| --- | --- | --- |
| Kamera `OFFLINE`, “Ulanib bo‘lmadi” | Manba yo‘q, USB uzilgan, RTSP xato | 19.2 ga qarang; `scripts\test_camera.py --source ...` |
| USB: `Camera index out of range` | Indeks mavjud emas | `python -c "import cv2;print(cv2.VideoCapture(0).read()[0])"`; `usb:0` qilib o‘zgartirish |
| Kamera `ONLINE`, lekin ism chiqmaydi | Shablon yo‘q / chegara baland / yuz kichik | Rasm qo‘shish, chegara 0.42, kameraga yaqinroq |
| `/review` bo‘sh | **Chiziq belgilanmagan** / kamera OFFLINE | `/cameras` → chiziq `102,446,1177,446` (1280×720) |
| “Kirdi”↔“Chiqdi” teskari | Chiziq yo‘nalishi | “Chiziqni teskari” katagi |
| Rasm “qabul qilinmadi” | Bir xil fayl, 15 chegara, yuz topilmadi, AI yo‘q | Xabar matnini o‘qing (10.1) |
| Yuz tanish “mavjud emas” | `insightface`/`onnxruntime` yo‘q | `pip install -r requirements-ai.txt` → serverni qayta ishga tushirish |
| `Maxsus YOLO modeli topilmadi` | `data/models/equipment_best.pt` yo‘q | Xato emas: `yolov8s.pt` ishlatiladi |
| `database is locked` | Ikki server bir bazada | Bitta server; testlarni to‘xtatib ishlatish |
| Port band | Boshqa jarayon | `run.py --port 8011` yoki jarayonni to‘xtatish |
| Login ishlamaydi (`production`) | HTTPS-only cookie | HTTPS yoki `APP_ENV=development` |
| Past FPS / issiq CPU | CPU inference | FPS 5–8, `YOLO_IMAGE_SIZE=480`, interval 15 |
| Sheets `FAILED` | Kalit/ID/ruxsat | 15-bo‘lim, `--status` |
| Tushunarsiz xato | Kutilmagan istisno | `logs\app.log` (traceback) yoki `logs\stderr.log` |

## 19.2. Kamera umuman ochilmayapti (bosqichma-bosqich)

1. **Boshqa dasturlarni yoping:** Windows Camera, Teams, Zoom, brauzerdagi video chat — kamera bitta
   dasturga tegishli.
2. **Qurilma holatini tekshiring** (PowerShell):

```powershell
Get-PnpDevice | Where-Object { $_.PNPClass -in @('Camera','Image') } |
  Select-Object FriendlyName, Status
```

`Status: OK` bo‘lishi kerak. `Unknown` / `Error` bo‘lsa — qurilma OS darajasida mavjud emas.

3. **To‘g‘ridan-to‘g‘ri sinash:**

```bat
.venv\Scripts\python.exe -c "import cv2; c=cv2.VideoCapture(0, cv2.CAP_DSHOW); print(c.isOpened())"
.venv\Scripts\python.exe -c "import cv2; c=cv2.VideoCapture(0); print(c.read()[0]); c.release()"
```

`False` — kamera boshqa dasturda band, USB’da muammo yoki indeks xato. `True` — manba ishlaydi,
demak tizimda manba (`usb:0`) yoki kamera holati xato.

4. **Kabelni boshqa USB portga** (to‘g‘ridan-to‘g‘ri kompyuterga) ulang; USB hub ishlatmang.
5. Device Manager → Cameras → qurilma → **Enable device**; keyin **Scan for hardware changes**.
6. Drayverni qayta o‘rnatish (masalan Logitech BCC950 uchun Logitech Capture/Firmware).
7. Kamera boshqa kompyuterda ham ishlamasa — **qurilma nosoz**, almashtirish kerak.

**Muhim:** server kamera qaytishini o‘zi kutadi — 5 → 60 s backoff bilan qayta ulanadi, serverni qayta
ishga tushirish shart emas. Kamera `ONLINE` bo‘lishi bilan `/live` da kadr paydo bo‘ladi.

## 19.3. “Hodisalar yozilmayapti” tekshiruv ro‘yxati

1. Kamera holati `ONLINE` mi? (`/cameras`)
2. Chiziq to‘ldirilganmi? (bo‘sh bo‘lsa kirim/chiqim yo‘q — `EQUIPMENT_PICKED_UP`
   esa chiziqsiz ham yoziladi)
3. Chiziq kadr o‘lchamiga mosmi? (`1280×720` uchun `102,446,1177,446`)
4. Odam chiziqni **to‘liq kesib** o‘tyaptimi (chiziqqa tegib qaytmasin)?
5. Cooldown o‘tganmi (20 s; qo‘lga olish hodisalarida 1 s)?
6. `logs/app.log` da `Trek ... bog'landi` yoki `UNKNOWN_PERSON` bormi?
7. Jihoz qo‘lga olinyaptimi? COCO model faqat `laptop`, `monitor/tv`, `cell phone`,
   `keyboard`, `mouse`, `person` ni taniydi — qolgan toifalar uchun 27 sinfli model
   kerak (20-bo‘lim).

## 19.4. “Yolg‘on hodisa ko‘p” tekshiruv ro‘yxati

1. Log’da `Aniqlik chegarasi ko‘tarildi: smartphone ... -> 0.75` qatorlari bormi? (11.1)
2. `/settings` → toifalar: `min_confidence` 11.1 dagi poldan past emasmi?
3. Kamera **yuz sathida** mi? Pastdan (stol ustidan) turgan kamera maus/klaviaturani
   telefonga o‘xshatadi.
4. Yorug‘lik yetarlimi? Qorong‘i kadrda ishonch sun’iy ravishda ko‘tariladi — IR/kunduz nur qo‘shing.
5. Keraksiz yozuvlarni `/review` da rad eting (sabab majburiy) yoki `/data` da ommaviy o‘chiring (9.13).
6. Qayta-qayta ‘qo‘l → telefon’ bo‘lsa: o‘sha toifa chegarasini 0.80 ga ko‘taring va 27 sinfli
   modelni ulang (20-bo‘lim) — COCO modeli bu ish uchun mo‘ljallanmagan.

## 19.5. Loglarni qanday o‘qish

```powershell
Get-Content logs\app.log -Tail 40
Select-String -Path logs\app.log -Pattern 'ERROR|WARN|OFFLINE|bog'  | Select-Object -Last 20
```

- `Kamera CAM holati: OFFLINE (...)` — manba uzilgan, sabab qavs ichida.
- `Event EVT-... CAMERA_DISCONNECTED` — tizim hodisasi (dalil rasmi bo‘lmaydi).
- `Trek 59 xodim bilan bog'landi: <Ism> (0.701)` — muvaffaqiyatli tanish (qavs ichida o‘xshashlik).
- `UNKNOWN_PERSON_*` — yuz topildi, lekin galereyada mos yo‘q.

---

# 20. Zaxira nusxa va tiklash

**Nimani zaxiralash kerak:**

| Nima | Yo‘l | Nega |
| --- | --- | --- |
| Baza | `data\inventory.db` | Xodimlar, shablonlar, inventar, hodisalar, audit |
| Rasmlar | `data\employees\` | Qayta yuklash uchun manba rasmlar |
| Sozlama | `.env` | Kalitlar va chegaralar |
| Model | `data\models\equipment_best.pt` | Custom YOLO (bo‘lsa) |

**Zaxira (server to‘xtatilganda):**

```powershell
$d = Get-Date -Format yyyyMMdd_HHmm
New-Item -ItemType Directory -Force "backup\$d" | Out-Null
Copy-Item data\inventory.db  "backup\$d\" -Force
Copy-Item .env               "backup\$d\" -Force
Compress-Archive -Path data\employees -DestinationPath "backup\$d\employees.zip" -Force
```

**Tiklash:** serverni to‘xtatish → `data\inventory.db` ni zaxira nusxasi bilan almashtirish →
`data\employees` ni ochish → serverni ishga tushirish → `/settings` → “Galereyani yangilash”.

> Baza SQLite (WAL rejimi). Zaxirani **server to‘xtatilganda** oling; ishlab turgan paytda nusxa
> ko‘chirilsa `-wal`/`-shm` fayllari hisobga olinmasligi mumkin.

---

# 21. Halol cheklovlar (nima nimaga bog‘liq)

**Yozilgan va tekshirilgan (avtomatik testlar + real server):**

- 10 sahifa, login/logout, rollar, CSRF, 404/403, audit yozuvlari.
- Inventar jurnali va balans formulalari; PENDING → tasdiq/rad oqimi.
- Hodisa dedupe/cooldown, bog‘lanish ballari, chiziq kesishish mantiqi.
- Yuz mosligi chegarasi/marjasi, xodim rasmlari (dedupe, 15 chegara), kameradan rasm olish.
- Kamera servisining uzilish/qayta ulanish xatti-harakati va simulyatsiya manbasi.
- Sheets upsert/dedupe/retry (soxta Google API bilan).

**Real jihoz/model talab qiladigan qismlar:**

| Nima | Nimaga bog‘liq |
| --- | --- |
| Real odamni tanib, ismini kadrda ko‘rsatish | Ishlaydigan kamera + `insightface` + shablonlar |
| Jihozni aniqlash (`printer`, `ups`, `scanner`) | Custom YOLO modeli (`data/models/equipment_best.pt`) |
| Kirim/chiqim hodisalari | Kamera + to‘g‘ri qo‘yilgan chiziq |
| Bir vaqtda ko‘p odam (5+) | Kamera o‘lchami/FPS va yuz o‘lchami |
| Yuqori FPS | GPU (`YOLO_DEVICE=cuda:0`, `FACE_PROVIDERS=CUDAExecutionProvider`) |

Bu cheklovlar “yashirin xato” emas: tizim ularni dashboard’da va logda ochiq yozadi. Kamera yo‘q bo‘lsa
u “kamera yo‘q” deydi — soxta natija ko‘rsatmaydi.

---

# 22. Tez-tez so‘raladigan savollar

**1. Serverni har safar qo‘lda ishga tushirishim kerakmi?**
Ha, hozircha xizmat (Windows service) sifatida o‘rnatilmagan. 8.2 dagi fon buyrug‘i bilan konsolsiz ishlatish
mumkin; kompyuter qayta yuklansa yana ishga tushirish kerak.

**2. Nega kamera “ONLINE”, lekin hech kim tanilmayapti?**
Shablon yo‘q yoki yuz kichik. `/employees` → “Kameradan rasm olish” (2–3 marta), keyin chegara 0.42.

**3. Rasm yukladim, “qabul qilinmadi” dedi. Nega?**
Xabar matniga qarang: bir xil fayl avval yuklangan (SHA-256), 15 talik chegara, yuz topilmadi,
yoki AI modeli yo‘q.

**4. `/review` bo‘sh. Nima qilay?**
Avval kamera uchun **chiziq** qo‘yilganini tekshiring (9.4). Chiziqsiz kirim/chiqim hodisasi yaratilmaydi.

**5. “Kirdi” o‘rniga “chiqdi” yozilyapti.**
`/cameras` → “Chiziqni teskari” ni yoqing (yo‘nalish almashtiriladi).

**6. Yangi toifa qo‘shdim, lekin jihoz aniqlanmayapti.**
YOLO modeli o‘sha sinfni bilmaydi. Modelni qayta o‘qitish kerak (`docs/yolo_training.md`), keyin sinf nomini
toifa formasidagi `YOLO sinflari` maydoniga yozish.

**7. Xodimni o‘chirsam tarix nima bo‘ladi?**
Eski hodisalar qoladi, lekin xodim bog‘lanishi bo‘shab “Noma’lum shaxs” ko‘rinishida turadi. Faqat
chiqarish kerak bo‘lsa — **bloklash** (tarix to‘liq saqlanadi).

**8. Baza qayerda va uni Excel’da ko‘rish mumkinmi?**
`data/inventory.db` (SQLite). `/reports` dagi CSV’ni Excel’da ochish qulay (BOM bilan, kirill/lotin to‘g‘ri).

**9. Internet kerakmi?**
Faqat birinchi o‘rnatishda (paketlar, `buffalo_l` modeli). Keyin butun tizim lokal ishlaydi.

**10. Kamera o‘rniga video fayl bilan sinash mumkinmi?**
Ha: manba turi `file`, manba `C:\video\kirish.mp4`. Chiziq va hodisalar xuddi kameraday ishlaydi.

---

# 23. Ilova: atamalar, statuslar, API

## 23.1. Kamera holatlari

| Holat | Ma’nosi |
| --- | --- |
| `CONNECTING` | Ulanish urinishi ketmoqda |
| `ONLINE` | Kadrlar kelyapti |
| `OFFLINE` | Ulanmadi; sabab qavs ichida, backoff 5→60 s |

## 23.2. Hodisa holatlari

| Holat | Ma’nosi |
| --- | --- |
| `AUTO` | Ishonchli, avtomatik qabul qilindi |
| `PENDING` | Tekshirish kutmoqda (inventarga ta’sir qilmaydi) |
| `CONFIRMED` | Tasdiqlandi (balans o‘zgardi) |
| `REJECTED` | Rad etildi (sabab bilan, balans tegilmaydi) |

## 23.3. Asosiy endpointlar

| Endpoint | Vazifasi |
| --- | --- |
| `GET /healthz` | Holat JSON (monitoring uchun) |
| `GET /api/cameras/<KOD>/stream` | MJPEG jonli oqim |
| `GET /api/cameras/<KOD>/snapshot` | JPEG kadr |
| `POST /api/cameras/<KOD>/update` | Kamera sozlamalarini saqlash (chiziq, manba) |
| `POST /api/employees/<id>/photos` | Rasm(lar) yuklash → shablon |
| `POST /api/employees/<id>/photos/capture` | **Kameradan rasm olish** → shablon |
| `POST /api/employees/<id>/photos/<file>/delete` | Shablonni o‘chirish |
| `POST /api/categories` | **Yangi texnika toifasi** |
| `POST /api/events/<id>/review` | Tasdiqlash / rad etish (rad etishda **sabab majburiy**) |
| `GET /api/events/<id>/evidence` | Dalil rasmi (operator+) |
| `POST /api/events/<id>/evidence/delete` | Faqat dalil rasmini o‘chirish (**admin**) |
| `POST /api/events/<id>/delete` | Hodisa + dalilni o‘chirish (**admin**, `CONFIRMED`/jurnalli qator himoyalangan) |
| `GET /data` | Tozalash sahifasi (**admin**) |
| `POST /api/data/evidence/purge` | `N` kundan eski dalillarni o‘chirish (**admin**) |
| `POST /api/data/evidence/orphans` | Egasisiz (orphan) fayllarni o‘chirish (**admin**) |
| `POST /api/data/events/purge` | Guruh/holat/yosh bo‘yicha ommaviy o‘chirish (**admin**, `OCHIRISH` so‘zi) |
| `GET /training` | Namuna rasmlar sahifasi (**admin**) |
| `POST /api/training/<kod>/samples` | Namuna rasm(lar) yuklash (**admin**, bir nechta fayl) |
| `POST /api/training/<kod>/capture` | Kameradan kadrni namuna qilib saqlash (**admin**) |
| `POST /api/training/<kod>/samples/<fayl>/delete` | Namuna rasmni o‘chirish (**admin**) |
| `GET /api/training/<kod>/samples/<fayl>` | Namuna rasmni ko‘rish (**admin**) |
| `GET /api/training/samples.zip` | Namunalar arxivi (`?code=` bilan bitta toifa) |
| `GET /reports/...csv` | CSV eksport |
| `GET /downloads` | Yuklab olish sahifasi (6 ta fayl) |
| `GET /downloads/all.xlsx` | Bitta faylda barcha ma’lumotlar |
| `GET /downloads/<fayl>.xlsx` / `.csv` | Alohida eksport (`?start=&end=`) |
| `POST /api/sheets/credentials` | Service account kalitini yuklash |
| `POST /api/sheets/spreadsheet` | Jadval ID/URL’ini ulash |
| `POST /api/sheets/test` | Ulanishni sinash |
| `POST /api/sheets/prepare` | 4 varaqni yaratish |
| `POST /api/sheets/toggle` | Avtomatik sinxronizatsiyani yoqish/o‘chirish |
| `POST /api/sheets/forget` | Kalitni o‘chirish (ulanishni uzish) |
| `POST /api/sheets/sync` | Qo‘lda sinxronlash |

## 23.4. Muhim fayl formatlari

| Fayl | Format |
| --- | --- |
| `data\employees\EMP002\reference_01.jpg` | JPEG, nomlash monoton (`reference_01`…`reference_15`) |
| `data\samples\mouse\mouse_001.jpg` | JPEG (92 sifat), namuna rasmlar; `MAX` 80 ta/toifa |
| `data\evidence\2026-10-28\EVT-XXXXXXXX.jpg` | JPEG, `EVIDENCE_JPEG_QUALITY` |
| `data\models\equipment_best.pt` | Ultralytics YOLO og‘irliklari |
| `logs\app.log` | Matnli log, rotatsiya bilan |
| `secrets\google_service_account.json` | Google service account kaliti (0600; git’ga qo‘shilmaydi) |
| `face-date_<fayl>_<sana>.xlsx` / `.csv` | Yuklab olingan eksport (brauzerning yuklamalar papkasida) |

---

# 24. Yakuniy nazorat ro‘yxati

**Har kuni (ish boshlanishida):**

- [ ] Server ishlayapti (`/healthz` → `"status":"ok"`)
- [ ] Kamera `ONLINE`
- [ ] Dashboard’da qizil ogohlantirish yo‘q
- [ ] `/review` dagi PENDING hodisalar ko‘rib chiqildi

**Haftada bir marta:**

- [ ] Yangi texniklar rasm bilan qo‘shildi (2–3 ta kameradan)
- [ ] `logs\app.log` da takroriy `OFFLINE`/`ERROR` yo‘q
- [ ] Inventar balansi aktivlar soniga mos (farq = 0)
- [ ] Baza va rasmlarning zaxira nusxasi olindi
- [ ] `/downloads` da `all.xlsx` ochiladi va qatorlar soni to‘g‘ri
- [ ] Google Sheets ishlatilsa: `/sheets` da **Sinovdan o‘tkazish** yashil, tarixda `FAILED` yo‘q

**Oyda bir marta:**

- [ ] Chegaralar qayta ko‘rib chiqildi (`FACE_SIMILARITY_THRESHOLD`, `YOLO_CONFIDENCE`)
- [ ] Dalillar arxivi tozalandi (`EVIDENCE_RETENTION_DAYS`)
- [ ] Parollar yangilandi, keraksiz foydalanuvchilar bloklandi
- [ ] Testlar ishlab turibdi: `pytest -q` → 276 passed
- [ ] YOLO uchun yangi kadrlar yig‘ildi: `/training` da chalkashadigan toifalarga namuna qo‘shildi (9.14)

---

*Qo‘llanma loyihaning haqiqiy holatiga mos yozilgan: 2026-yil 30-oktabr, testlar 276 passed.
Savol tug‘ilsa — birinchi navbatda `logs\app.log` va `/healthz` ni tekshiring: ular sababni aniq ko‘rsatadi.*
