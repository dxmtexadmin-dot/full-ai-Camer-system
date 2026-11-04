# AI CCTV — xodimlarni tanish va jihozlar inventarizatsiyasi

Lokal (on-premise) tizim: IP/USB kamera oqimidan xodimlarni yuz bo‘yicha taniydi,
jihozlarni YOLO bilan aniqlaydi, virtual chiziqdan o‘tishni qayd etadi va
kim nimani olib kirgani/chiqarganini inventar jurnaliga yozadi.

Asosiy tamoyillar:

* **Ma’lumot lokal** — SQLite bazasi, dalil rasmlari va loglar shu kompyuterda.
  Google Sheets faqat **hisobot oynasi** (mirror), hech qachon asosiy baza emas.
* **Avtomatika taxmin qilmaydi** — ishonchsiz holatlar `PENDING` bo‘lib qoladi va
  operator tasdiqlaguncha inventar qoldig‘iga ta’sir qilmaydi.
* **Hamma narsa jurnalga yoziladi** — inventar harakatlari faqat qo‘shiladi
  (append-only), o‘chirilmaydi; foydalanuvchi amallari audit jurnalida.

Interfeys va hujjatlar **o‘zbek lotin** alifbosida.

---

## 1. Imkoniyatlar

| Yo‘nalish | Nima qilingan |
| --- | --- |
| Kamera | RTSP / USB / video fayl / simulyatsiya; avtomatik qayta ulanish (eksponensial backoff); MJPEG jonli oqim |
| Aniqlash | Ultralytics YOLO (model yo‘li, o‘lcham, ishonch chegarasi sozlanadi) + ByteTrack trek ID; toifa bo‘yicha **qat’iy ishonch poling** (0.60–0.75), shakl (nisbat/maydon) filtri va “maus ↔ telefon” chalkashuvini yechish |
| Yuz tanish | InsightFace (`buffalo_l`) + ONNX Runtime; bir xodim uchun bir nechta shablon; paneldan **kameradan rasm olish**; o‘xshashlik chegarasi va “ikkinchi nomzod” marjasi; `Noma’lum shaxs` |
| Chiziq | Virtual chiziq (2 nuqta), histerezis, cooldown, yo‘nalishni teskari qilish; kirim/chiqim |
| Bog‘lanish | “Kim olib o‘tdi” — olib yurish + yaqinlik + vaqt + yo‘nalish ballari; `STRONG/WEAK/AMBIGUOUS/NONE`; shubhali holatda `Tekshirish kerak` |
| Inventar | Append-only jurnal: boshlang‘ich qoldiq, kirim, chiqim, qaytarish, ichki ko‘chirish, tuzatish; `PENDING` nomzodlar alohida; **yangi texnika toifasini** paneldan qo‘shish |
| Hodisalar | Dublikat filtr (`Event ID` + cooldown oynasi), dalil rasmi, tekshirish (review) oqimi |
| Hisobot | Filtrli jadvallar, CSV (Excel uchun BOM bilan), JSON API, Google Sheets sinxronizatsiyasi |
| Xavfsizlik | Sessiya + CSRF, rollar (admin/operator/viewer), PBKDF2 parol xeshi, dalil fayllariga faqat ruxsatli kirish, audit jurnali |
| Tozalash | Admin uchun `/data`: eski dalillar, egasiz fayllar, “shovqin” hodisalarni ommaviy o‘chirish; `OCHIRISH` so‘zi bilan tasdiqlash; `CONFIRMED` va jurnalga bog‘langan qatorlar himoyalangan |
| O‘qitish | Admin uchun `/training`: har bir toifa uchun **10–15 ta namuna rasm** (yuklash yoki kameradan kadr), nusxa filtr, thumbnail ro‘yxati, ZIP eksport; `--import-samples` bilan datasetga qo‘shish |
| Ishga tushirish | `run.bat` (Windows), `run.sh` (Linux/macOS), `run.py` CLI |

---

## 2. Talablar

* **Python 3.11** (64-bit). Boshqa versiyalar bilan AI paketlari (torch/insightface)
  g‘ildiraklari mos kelmasligi mumkin.
* Windows 10/11 yoki Linux/macOS.
* Kamera: RTSP (H.264/H.265) yoki USB web-kamera. Sinov uchun kamera shart emas —
  simulyatsiya rejimi bor.
* Resurslar: CPU rejimida kamida 4 GB RAM (8 GB tavsiya), 2 GB bo‘sh disk
  (`buffalo_l` modeli va `torch` uchun). GPU (CUDA) ixtiyoriy, lekin FPS uchun foydali.
* Tarmoq: faqat AI modellari va Google Sheets uchun internet kerak. Asosiy ish
  internetsiz ham davom etadi.

---

## 3. O‘rnatish

### 3.1. Windows — eng oson yo‘l

```bat
run.bat
```

Skript `.venv` yaratadi, `.env` ni `.env.example` dan nusxalaydi, `requirements.txt`
ni o‘rnatadi va serverni ko‘tardi. Brauzerda <http://127.0.0.1:8000> oching.

> Birinchi ishga tushirishdan oldin `.env` dagi `APP_SECRET_KEY` va
> `BOOTSTRAP_ADMIN_PASSWORD` ni o‘zgartiring (skript buni eslatadi).

### 3.2. Linux / macOS

```bash
chmod +x run.sh
./run.sh
```

### 3.3. Qo‘lda o‘rnatish (istalgan OS)

Windows (PowerShell yoki cmd):

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python.exe run.py
```

Linux/macOS:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python run.py
```

`run.py` parametrlari: `--host`, `--port`, `--reload`, `--log-level`.
Masalan: `.venv\Scripts\python.exe run.py --port 9000 --reload`.

### 3.4. AI paketlari (kamera inference uchun)

Dashboard, inventar va tekshirish AI paketlarisiz ham ishlaydi. Real deteksiya va
yuz tanish uchun:

```bat
.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
```

Eslatmalar:

* `ultralytics` `torch` ni tortadi (CPU g‘ildiragi ~250 MB; to‘liq o‘rnatish ~1–2 GB).
* `insightface` versiyasi **2.1** — bu `py3-none-any` g‘ildirak, C++ kompilyator
  talab qilmaydi. (Ilgari tavsiya etilgan 0.7.x PyPI’da faqat manba kodi bilan
  tarqaladi va Windows’da “Visual Studio Build Tools” + Cython kerak bo‘ladi —
  shuning uchun 0.7.x ni qo‘lda tanlamang.)
* O‘rnatilgandan keyin **serverni qayta ishga tushiring**: paketlar jarayon
  ishga tushganda import qilinadi.
* `buffalo_l` modeli (~280 MB: detection, landmark, recognition, genderage)
  birinchi marta ishlatilganda yuklab olinadi va `~/.insightface/models/buffalo_l`
  ichida saqlanadi. Internet bo‘lmasa, papkani boshqa mashinadan ko‘chirib qo‘yish
  mumkin.
* `buffalo_l` InsightFace’ning **tadqiqot uchun bepul** modeli; tijoriy
  foydalanishdan oldin litsenziyani tekshiring.
* O‘rnatilmasa yoki model yuklanmasa tizim ishlashda davom etadi va yuz tanishni
  “mavjud emas” deb halol ko‘rsatadi (yolg‘on “ishlayapti” holati yo‘q).
* CUDA uchun `onnxruntime` o‘rniga `onnxruntime-gpu` o‘rnating va `.env` da
  `FACE_PROVIDERS=CUDAExecutionProvider`.

---

## 4. Birinchi ishga tushirish

1. `.env` faylini to‘ldiring (kamida `APP_SECRET_KEY`, `BOOTSTRAP_ADMIN_PASSWORD`).
2. `run.bat` yoki `run.py` ni ishga tushiring. Baza `data/inventory.db` da
   avtomatik yaratiladi: 27 ta toifa (`person` + 26 ta jihoz toifasi, jumladan
   televizor, IP telefon, statsionar telefon, smartfon, planshet, kamera, mini IP
   kamera, LAN tester, LAN kabel, proyektor, server) va **o‘chirilgan** namuna
   kamera `SIM-01` (simulyatsiya) qo‘shiladi. Yangi toifalar mavjud bazaga ham
   **avtomatik qo‘shiladi** — qayta o‘rnatish shart emas.
3. <http://127.0.0.1:8000> → login sahifasi. Birinchi admin `.env` dagi
   `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` (standart:
   `admin` / `Admin123!`). Admin faqat **foydalanuvchilar jadvali bo‘sh bo‘lganda**
   yaratiladi.
4. Sozlamalar → “Foydalanuvchilar” bo‘limida operator/kuzatuvchi hisoblarini
   yarating va admin parolini almashtiring.
5. Bosh sahifada konfiguratsiya ogohlantirishlari ko‘rsatiladi (masalan
   `Maxsus YOLO modeli topilmadi`, `APP_SECRET_KEY o‘zgartirilmagan`). Ularni yo‘q qiling.

`/healthz` — monitoring holati (JSON), tashqi nazorat tizimlari uchun.

---

## 5. `.env` sozlamalari

To‘liq ro‘yxat va izohlar `.env.example` da. Asosiylari:

| Kalit | Ma’nosi |
| --- | --- |
| `APP_ENV` | `development` yoki `production`. `production` da sessiya cookie faqat HTTPS orqali yuboriladi |
| `APP_HOST`, `APP_PORT` | Server manzili (standart `127.0.0.1:8000`) |
| `APP_TIMEZONE` | Mahalliy vaqt zonasi (`Asia/Tashkent`) |
| `DATABASE_URL` | SQLite yo‘li. Model’lar portativ — PostgreSQL’ga ko‘chirish mumkin |
| `APP_SECRET_KEY` | Sessiya imzosi kaliti. **Majburiy o‘zgartiriladi** |
| `SESSION_MAX_AGE_SECONDS` | Sessiya muddati (standart 12 soat) |
| `BOOTSTRAP_ADMIN_*` | Birinchi admin (faqat bo‘sh baza uchun) |
| `YOLO_MODEL_PATH` | O‘qitilgan model, standart `data/models/equipment_best.pt` |
| `YOLO_FALLBACK_MODEL` | Model topilmasa ishlatiladigan model (hozir `data/models/yolo26x.pt`, orqaga qaytarish: `yolov8s.pt`) |
| `YOLO_CONFIDENCE`, `YOLO_IMAGE_SIZE`, `YOLO_DEVICE` | Deteksiya chegarasi, inference o‘lchami, `cpu`/`cuda:0` |
| `YOLO_DEDUPE_IOU` | Bir obyekt ikki sinf sifatida topilsa (masalan `tv` + `monitor`), IoU shu chegaradan yuqori bo‘lsa faqat ishonchlirog‘i qoladi (standart `0.6`) |
| `FACE_SIMILARITY_THRESHOLD` | Yuz mosligi chegarasi (0.45 atrofida) |
| `FACE_MATCH_MARGIN` | Eng yaxshi va ikkinchi nomzod orasidagi minimal farq |
| `FACE_RECOGNITION_INTERVAL` | Har necha kadrda bir marta yuz tekshiriladi |
| `PROCESSING_FPS`, `PROCESSING_WIDTH`, `PROCESSING_HEIGHT` | Kadr qayta ishlash parametrlari (chiziq koordinatalari shu o‘lchamda) |
| `CAMERA_RECONNECT_INTERVAL`, `CAMERA_MAX_RETRY_INTERVAL` | Qayta ulanish backoff sozlamalari |
| `SIMULATION_MODE` | `true` bo‘lsa sintetik manba ishlatiladi |
| `EVENT_COOLDOWN_SECONDS` | Bir xil hodisani takror yozmaslik oynasi |
| `EVIDENCE_ENABLED`, `EVIDENCE_RETENTION_DAYS`, `EVIDENCE_JPEG_QUALITY` | Dalil rasmlari |
| `GOOGLE_*` | Sheets integratsiyasi (7-bo‘lim) |
| `LOG_LEVEL`, `LOG_DIR` | Loglar (`logs/app.log`) |

**Sozlamalar sahifasi** (`/settings`, admin) `.env` dagi oq ro‘yxatdagi
qiymatlarni tahrirlaydi: model yo‘li, chegaralar, FPS, o‘lchamlar, Sheets
yoqish/o‘chirish va h.k. Fayl atomik yoziladi, jarayon ichida qayta yuklanadi va
kameralar qayta ishga tushiriladi. Maxfiy kalitlar (`APP_SECRET_KEY`,
`DATABASE_URL`, bootstrap paroli) ataylab UI’dan tahrirlanmaydi.

---

## 6. Rollar va sahifalar

| Sahifa | URL | Kim ko‘radi |
| --- | --- | --- |
| Bosh sahifa | `/` | barcha rollar |
| Jonli kamera | `/live` | barcha rollar |
| Harakatlar jurnali | `/movements` | barcha rollar |
| Inventar | `/inventory` | barcha rollar (amallar — operator+) |
| Hisobotlar | `/reports` | barcha rollar |
| Tekshirish (review) | `/review` | operator, admin |
| Xodimlar | `/employees` | faqat admin |
| Jihozlar va toifalar | `/equipment` | faqat admin |
| Kameralar | `/cameras` | faqat admin |
| Google Sheets (ulash) | `/sheets` | faqat admin |
| Sozlamalar | `/settings` | faqat admin |
| Yuklab olish (Excel/CSV) | `/downloads` | barcha rollar (audit fayli — admin) |
| Ma’lumotlarni tozalash | `/data` | faqat admin |
| Namuna rasmlar (o‘qitish) | `/training` | faqat admin |

Ruxsatlar qisqacha:

* **Administrator** — hamma narsa: kameralar, xodimlar va shablonlar, toifalar,
  foydalanuvchilar, `INITIAL` (boshlang‘ich qoldiq), sozlamalar, modelni qayta yuklash,
  **dalil/hodisalarni o‘chirish, `/data` tozalash va `/training` namunalari**.
* **Operator** — tekshirish (tasdiqlash/rad etish, xodim/jihozni bog‘lash), inventar
  amallari (boshlang‘ich qoldiqdan tashqari), dalil rasmlarini ko‘rish, Sheets’ni
  qo‘lda sinxronlash. **O‘chirish huquqi yo‘q** (403).
* **Kuzatuvchi** — faqat o‘qish: dashboard, jonli oqim, jurnal, inventar, hisobot, CSV.

Har bir POST so‘rov CSRF token bilan himoyalangan; parollar PBKDF2 bilan xeshlanadi;
`/api/events/{id}/evidence` faqat operator va adminga ochiq, fayl yo‘li tekshiriladi.

---

## 7. Kameralar

`/cameras` (admin) → “Kamera qo‘shish”.

| Maydon | Izoh |
| --- | --- |
| Kod | Qisqa unikal kod (masalan `CAM-01`, faqat harf/raqam/`-`/`_`) |
| Nom, joylashuv | Odam o‘qiydigan tavsif |
| Manba turi | `rtsp`, `usb`, `file`, `simulation` |
| Manba | RTSP URL / qurilma raqami / fayl yo‘li |
| Chiziq (x1,y1,x2,y2) | Virtual chiziq koordinatalari (piksellarda, `PROCESSING_WIDTH`×`PROCESSING_HEIGHT` kadri bo‘yicha) |
| Chiziqni teskari | Kirim/chiqim yo‘nalishini almashtiradi |
| Ishonch chegarasi | Shu kamera uchun umumiy chegarani bekor qiladi (bo‘sh bo‘lsa global) |
| Yoqilgan | Saqlanganda darhol ishga tushadi |

Manba namunalari:

```
rtsp://admin:parol@192.168.1.64:554/Streaming/Channels/101
usb:0
C:\video\kirish.mp4
```

Simulyatsiya: manba turi `simulation` (manba bo‘sh). Kadrda xona, odam va vaqti-vaqti
laptop chiziladi; overlay’da **SIMULYATSIYA** yozuvi turadi — buni real natija deb
o‘ylash mumkin emas. Bu rejim kamerasiz oqimni uchdan-uchiga sinash uchun.

Amaliy maslahatlar:

* Chiziq **yurish yo‘nalishiga perpendikulyar**, eshik oldidan 1–1.5 m ichkarida
  bo‘lsin; odam chiziqni to‘liq kesib o‘tishi kerak.
* Chiziq koordinatalarini kadr o‘lchamiga moslang: `scripts/test_camera.py`
  yordamida kadr saqlab, piksel o‘lchamini aniqlang (u ham `PROCESSING_*` ga
  keltiriladi).
* Kamera balandligi va burchagi o‘zgarsa — chiziqni va (keyinchalik) modelni qayta
  kalibrlang.
* Holat ustuni: `CONNECTING → ONLINE` yoki `OFFLINE` + sabab. Uzilishda
  `CAMERA_DISCONNECTED`, tiklanishda `CAMERA_RECONNECTED` hodisasi yoziladi.
* Jonli oqim: `/live` sahifasi yoki `GET /api/cameras/<KOD>/stream` (MJPEG),
  `GET /api/cameras/<KOD>/snapshot` (JPEG).

---

## 8. Xodimlar va yuz tanish

Ro‘yxatga olishning ikki yo‘li bor — ikkalasi ham bir xil servislardan foydalanadi,
natija darhol UI’da ko‘rinadi.

**Veb:** `/employees` → “Xodim qo‘shish” → rasm(lar) yuklash.

**CLI:**

```bat
.venv\Scripts\python.exe scripts\register_employee.py --list
.venv\Scripts\python.exe scripts\register_employee.py --first-name Ali --last-name Valiyev ^
    --department "IT bo‘limi" --photos C:\rasm\ali1.jpg C:\rasm\ali2.jpg C:\rasm\ali3.jpg
.venv\Scripts\python.exe scripts\register_employee.py --add-photos EMP001 --photos yangi.jpg
```

**Ko‘p xodimni birdaniga (masalan, texniklarni) — papkalardan:**

Har xodim uchun alohida papka, nomi «Familiya Ism», ichida 2–5 ta rasm:

```text
C:\texniklar\
    Anvarov Anvar\  1.jpg  2.jpg  3.jpg
    Karimov Sardor\ 1.jpg  2.jpg
```

```bat
.venv\Scripts\python.exe scripts\enroll_employees.py --folder "C:\texniklar" --dry-run
.venv\Scripts\python.exe scripts\enroll_employees.py --folder "C:\texniklar" --department "Texniklar"
.venv\Scripts\python.exe scripts\enroll_employees.py --check
```

* `--dry-run` — faqat kim qo‘shilishini ko‘rsatadi, bazaga yozmaydi;
* `--given-first` — papka nomi «Ism Familiya» tartibida bo‘lsa;
* `--max-photos 5` — har xodim uchun olinadigan rasm soni (maksimal 15);
* skriptni qayta ishga tushirish xavfsiz: mavjud xodim ismi bo‘yicha topiladi va
  rasmlar o‘shanga qo‘shiladi (bir xil rasmlar tashlab yuboriladi);
* oxirida `--check` hisoboti chiqadi: ikki xodimning shablonlari bir-biriga juda
  o‘xshasa (0.60 dan yuqori), ogohlantirish beriladi — bu bitta odam ikki marta
  kiritilgan bo‘lishi mumkin, aks holda kamera adashtiradi.
* ishlab turgan server galereyani **o‘zi** yangilaydi (20 soniya ichida), qayta
  ishga tushirish shart emas.

Shablon (reference) talablari:

* Har xodim uchun **3–5 ta** rasm: old tomondan, ozgina chap/o‘ng burilish, turli
  yorug‘lik. Ko‘zoynak/kepka kiygan holatlar ham bo‘lsa yaxshi.
* Bitta rasmda bitta yuz, kamida ~120×120 piksel, loyqa emas.
* Rasmlarni **shu kameradan** olish tavsiya etiladi (burchak va yorug‘lik mos bo‘lsin).
* Rasm yuklangach “Yuz galereyasi” avtomatik yangilanadi; qo‘lda:
  Sozlamalar → “Galereyani yangilash”.
* Bitta xodim uchun jami **15 tagacha** rasm saqlanadi. Bir xil rasm (baytma-bayt
  bir xil fayl) ikkinchi marta qabul qilinmaydi — forma tasodifan qayta yuborilsa
  ham nusxalar to‘planmaydi. Yuklash tugmasi yuborilganda “Yuborilmoqda...”
  ko‘rinishida bloklanadi.

**Kameradan rasm olish (paneldan):** `/employees/<KOD>` → “Kameradan rasm olish”.
Kadr 4 soniyada bir yangilanadi; xodim kameraga qarab turganda tugma bosiladi va
kadrning eng katta yuzi kesib olinib, yangi shablon sifatida saqlanadi.

* Bu usul eng tez aniqlik oshiradi: rasm burchagi, obyektiv va yorug‘lik kamera
  bilan bir xil bo‘ladi (skanerlangan suratdan ko‘ra ancha yaxshi moslik beradi).
* Kadrda bir nechta yuz bo‘lsa, eng kattasi olinadi va ogohlantirish chiqadi.
* Har bosishda bitta shablon qo‘shiladi; 15 ta chegarasi va dublikat filtri
  bu yerda ham ishlaydi.
* Kamera uzilgan bo‘lsa: “Kameradan kadr olinmadi” xabari chiqadi (kamera
  holatini `/cameras` sahifasida tekshiring).

Chegaralar:

* `FACE_SIMILARITY_THRESHOLD` — past = ko‘proq noto‘g‘ri moslik, yuqori = ko‘proq
  “Noma’lum shaxs”. 0.40–0.55 oralig‘ida real kadrlarda sinab ko‘ring.
* `FACE_MATCH_MARGIN` — bir-biriga o‘xshash xodimlar orasida “taxmin qilmaslik”
  uchun minimal farq. Shubhali holat `PENDING` bo‘lib qoladi.
* Taniqlilik ishonchsiz bo‘lsa hodisa `UNKNOWN_PERSON_*` va `PENDING` bo‘ladi —
  operator keyin xodimni qo‘lda bog‘laydi.

Xodimni bloklash (is_active=false) uni galereyadan chiqaradi, lekin tarixiy
hodisalar saqlanadi. O‘chirish biometrik shablonlar va rasm papkasini ham olib
tashlaydi; eski hodisalarda xodim bog‘lanishi bo‘shab, ular “Noma’lum shaxs”
ko‘rinishida qoladi (FK `SET NULL`).

---

## 9. Inventar qoidalari (append-only jurnal)

Har bir harakat `inventory_transactions` jadvaliga **yangi qator** sifatida yoziladi;
qatorlar tahrirlanmaydi va o‘chirilmaydi. Joriy qoldiq har doim yig‘indi sifatida
hisoblanadi.

| Amal | Ta’siri | Kim |
| --- | --- | --- |
| `INITIAL` (boshlang‘ich qoldiq) | `+N`, faqat bir marta kiritish tavsiya etiladi | admin |
| `RECEIVED` (kirim) | `+N` | operator+ |
| `REMOVED` (chiqim) | `-N` | operator+ |
| `RETURNED` (qaytarish) | `+N` | operator+ |
| `TRANSFER` (ichki ko‘chirish) | Soni o‘zgarmaydi, joylashuv yoziladi | operator+ |
| `CORRECTION` (tuzatish) | `±N` (sabab majburiy) | operator+ |
| Kamera hodisasi (jihoz olib o‘tildi) | `PENDING` nomzod: `+1`/`-1`, **tasdiqlanmagan** | avtomatik |

Balans ko‘rsatkichlari (`/inventory`):

* **Kutilgan (expected)** — tasdiqlangan qatorlar yig‘indisi (INITIAL ham ichida).
* **Kirim kutilmoqda / Chiqim kutilmoqda** — tasdiqlanmagan (PENDING) nomzodlar.
* **Prognoz (projected)** — `expected + pending_in − pending_out`.
* **Ro‘yxatga olingan aktivlar** — `Jihozlar` bo‘limidagi inventar raqamlari soni.
* **Farq (discrepancy)** — aktivlar ro‘yxatga olingan bo‘lsa `expected − aktivlar`.
* **Muammo bor** — `pending_out > 0` yoki farq noldan boshqacha.

Muhim: **rad etilgan** hodisa jurnalda audit uchun qoladi, lekin na balansga, na
“kutilmoqda” ko‘rsatkichiga qo‘shiladi (texnik jihatdan `is_rejected` belgisi).

---

## 10. Tekshirish (review) va “kim olib o‘tdi” bog‘lanishi

Jihoz harakati hodisasi **har doim** `PENDING` yaratiladi — inventar faqat operator
tasdiqlagach o‘zgaradi.

Bog‘lanish ballari (0..1):

```
0.50 × olib yurish (odam va jihoz trek’lari birga harakatlanishi)
0.25 × yaqinlik (box’lar masofasi)
0.15 × vaqtinchalik moslik
0.10 × yo‘nalish mosligi
```

Natija sifati: `STRONG` (≥ 0.65 va trek yetarlicha uzun), `WEAK`, `AMBIGUOUS`
(eng yaxshi va ikkinchi nomzod farqi kichik), `NONE`. `WEAK`/`AMBIGUOUS`, qisqa
trek yoki xodim ishonchsiz bo‘lsa — hodisa izohida `Tekshirish kerak` yoziladi va
`extra.requires_review=true` saqlanadi (hodisa `PENDING` bo‘lib qoladi).

Operator `/review` sahifasida:

* **Tasdiqlash** — hodisa `CONFIRMED`; unga bog‘liq jurnal qatorlari tasdiqlanadi
  (balans o‘zgaradi). Xodim/jihoz/kategoriyani shu yerda aniqlashtirish mumkin.
* **Rad etish** — sabab **majburiy**. Bo‘sh sabab bilan forma yuborilmaydi: maydon
  ostida qizil ogohlantirish chiqadi (`Rad etish uchun sabab kiritilishi shart`),
  server ham bo‘sh sababni qabul qilmaydi (`event_service.review_event`).
  Qatorlar `is_rejected` bo‘ladi, balans tegilmaydi; sabab `notes` va `audit_logs` ga yoziladi.
* Dalil rasmi va kamera/vaqt ma’lumotlari ko‘rinadi.
* **Admin** uchun qo‘shimcha ikki tugma: *Dalilni o‘chirish* (faqat JPEG) va
  *Hodisani o‘chirish* (hodisa + dalil). `CONFIRMED` va inventar jurnaliga bog‘langan
  hodisalar o‘chirilmaydi — qarang: 11.3.

`/movements` — barcha hodisalar jurnali (filtrlar bilan), `/reports` — jamlanma
hisobotlar.

**`/review` bo‘sh bo‘lsa:** kamera uchun virtual **chiziq belgilanmagan** bo‘lishi
mumkin — chiziqsiz kirim/chiqim hodisalari yaratilmaydi (kamera ishlab, yuz
tanilsa ham). `/cameras` → kamera → “Chiziq” maydonlarini to‘ldiring (kadr
o‘lchamiga mos: masalan 1280×720 uchun `102,446,1177,446`), saqlang. Chiziq
qayta ishga tushirilgach, o‘tishlar `PERSON_ENTERED/EXITED` bo‘lib shu sahifaga
tushadi.

---

## 11. Hodisa turlari va dublikatlar

Turlar: `PERSON_ENTERED/EXITED`, `UNKNOWN_PERSON_ENTERED/EXITED`,
`UNKNOWN_PERSON_DETECTED`, `EQUIPMENT_PICKED_UP`, `EQUIPMENT_PUT_DOWN`,
`EQUIPMENT_BROUGHT_IN`, `EQUIPMENT_TAKEN_OUT`,
`EQUIPMENT_RETURNED`, `INVENTORY_DISCREPANCY`, `CAMERA_DISCONNECTED`,
`CAMERA_RECONNECTED` (`REQUIRES_REVIEW` va `EQUIPMENT_RETURNED` turlari mavjud,
lekin hozirgi avtomatik oqimda generatsiya qilinmaydi — tekshirish bayrog‘i
hodisaning `extra` maydonida saqlanadi).

### 11.1. Jihoz “qo‘lda” zanjiri (pick-up → put-down)

Jihoz hodisasi endi **chiziq kesilishini kutmaydi**: kamera `ONLINE` bo‘lishi bilan
ishlaydi.

1. **Biriktirish** — odam qutisi ichida (35 % kengaytirilgan) jihoz qutisi markazi
   ketma-ket **3 kadr** tursa, `EQUIPMENT_PICKED_UP` yoziladi: jihoz trek raqami
   (`object_track_id`) shu odam trekiga (`person_track_id`) bog‘lanadi. Tasodifiy
   yonma-yon turish (javon, stol) biriktirilmaydi — faqat tana bilan qoplanish.
2. **Ushlab turish** — bog‘lanish qisqa uzilishlarga (bir necha kadr) chidaydi;
   davomida xodim yuzi qayta tanilsa, xodim maydoni yangilanadi.
3. **Qo‘yish** — jihoz **6 s** davomida ko‘rinib turadi, lekin hech kimning tanasi
   bilan qoplanmasa, `EQUIPMENT_PUT_DOWN` yoziladi (qancha ushlangani `held_seconds`).
   Jihoz kadrdan butunlay chiqib ketsa, put-down yozilmaydi — o‘rniga kirim/chiqim
   (`EQUIPMENT_TAKEN_OUT` / `EQUIPMENT_BROUGHT_IN`) hodisasi chiqadi.
4. **Chiqish/kirish** — chiziq kesilganda ushlab turilgan jihoz **egasi bilan birga**
   yoziladi: xodim, `held_since`, `held_seconds`, dalil kadri va vaqt. Jihoz o‘sha
   paytda kadrda ko‘rinmasa ham (tana ortida, sumkada), biriktirish **45 s**
   eslab qolinadi va hodisa baribir yoziladi (izohda “kadrda ko‘rinmadi”).

`EQUIPMENT_PICKED_UP` / `EQUIPMENT_PUT_DOWN` — **kuzatuv** hodisalari, ular inventar
jurnaliga nomzod yaratmaydi (`POSSESSION_EVENT_TYPES`). Jurnal faqat kirim/chiqimdan
o‘zgaradi. `/reports` dagi “Hodisalar (birinchi 200)” kartasi faqat shu biznes
turlarini ko‘rsatadi — kamera uzilish shovqini jadvalni to‘ldirmaydi (to‘liq tarix
`/movements` da). Dashboard’dagi **“Hozir qo‘lda”** kartasi ayni damda kim nimani
ushlab turganini real vaqtda ko‘rsatadi.

Dublikat oldini olish ikki qatlamda:

1. **Dedupe kaliti** (`mv:` / `pp:` / `pos:` / `sys:`) — bir xil trek/kamera/kategoriya
   uchun takror yozilmaydi. `pos:` kaliti jihoz + odam juftini saqlaydi — bir sekundda
   ikki xil jihoz ko‘tarilsa, ular bir-birini bekor qilmaydi.
2. **Cooldown oynasi** (`EVENT_COOLDOWN_SECONDS`, standart 20 s) — kalit vaqt
   oynasi bilan birga hisoblanadi; tez-tez takrorlanish to‘xtaydi.

Xodim aniq tanilsa `PERSON_*` hodisasi `AUTO`; noma’lum shaxs `PENDING` bo‘ladi.
Kamera uzilishi/tiklanishi kabi tizim hodisalarida dalil rasmi bo‘lmaydi.

### 11.2. Aniqlik: ishonch poling va shakl qoidalari

“Maus telefon deb yozilyapti”, “quloq yonidagi qo‘l smartfon bo‘lib qolyapti” shikoyatining
sababi — juda past chegara (`yolov8s` stol usti mayda narsalarni 0.30–0.45 bilan belgilaydi)
va hech qanday geometrik tekshiruv yo‘qligi. Endi:

| Toifalar | Eng past ishonch |
| --- | --- |
| `smartphone`, `tablet`, `ip_phone`, `landline_phone`, `mini_camera` | 0.75 |
| `camera`, `mouse`, `keyboard`, `flash_drive`, `external_hdd`, `lan_tester`, `lan_cable`, `other_equipment` | 0.70 |
| qolgan jihoz toifalari | 0.60 |
| `person` | 0.45 (o‘zgarmaydi) |

* `bootstrap.enforce_confidence_floors()` har ishga tushishda chegara **faqat ko‘taradi**
  (log: `Aniqlik chegarasi ko‘tarildi: smartphone 0.31 -> 0.75`).
* `yolo_service.SHAPE_RULES` — toifa bo‘yicha ruxsat etilgan **nisbat** (kenglik/balandlik)
  va **kadrdagi maydon ulushi**; mos kelmagan quti tashlanadi (`plausible_box`).
  Masalan `smartphone`: nisbat 0.28–1.25, maydon 0.12 %–10 %.
* `yolo_service.resolve_confusions()` — bir joyda `smartphone` va `mouse`/`keyboard`
  qutilari ustma-ust tushsa (IoU ≥ 0.35), **telefon belgisi tashlanadi**: stol jihozi
  telefonga aylanmaydi.

Shakl qoidalari jadvali va sozlash yo‘llari: `docs/yoriqnoma_uz.md`, 11.1.

### 11.3. Dalil va hodisalarni o‘chirish (faqat admin)

Uch daraja; hammasi CSRF bilan himoyalangan va `audit_logs` ga yoziladi:

| Daraja | Qayerda | Nima o‘chadi |
| --- | --- | --- |
| Dalil (JPEG) | `/review` kartasi | faqat rasm fayli |
| Bitta hodisa | `/review` kartasi | hodisa + rasmi |
| Ommaviy | `/data` | guruh/holat/yosh bo‘yicha qatorlar + rasmlar; eski dalillar; egasiz fayllar |

Ommaviy amallar `OCHIRISH` so‘zini talab qiladi (xato yozilsa hech narsa o‘chmaydi).
**Himoya:** `CONFIRMED` hodisalar va `inventory_transactions` ga bog‘langan hodisalar
(`EQUIPMENT_TAKEN_OUT` va h.k.) o‘chirilmaydi — append-only jurnal buzilmaydi.
Kerak bo‘lsa avval `/review` da rad eting yoki jurnalda `CORRECTION` yozing.
Operator `/data` sahifasiga kira olmaydi (403) va `/review` dagi o‘chirish tugmalarini
ko‘rmaydi.

### 11.4. Namuna rasmlar va qayta o‘qitish (`/training`)

Chegara va shakl qoidalari yolg‘on hodisani **kamaytiradi**, modelni esa **o‘zgartirmaydi**.
Shuning uchun admin `/training` sahifasida har bir chalkashadigan toifa uchun haqiqiy rasmlarni
yig‘adi (maus, monitor, klaviatura, telefon…):

* yuklash — bir vaqtda bir nechta fayl (JPG/PNG/WEBP, 8 MB gacha), JPEG’ga keltirilib saqlanadi;
* **kameradan kadr** — aynan shu linza, burchak va yorug‘likdagi namuna;
* nusxa (SHA-256) ikki marta saqlanmaydi, toifada ko‘pi bilan 80 ta, maqsad — **10–15 ta**;
* har bir amal auditga yoziladi: `samples.add`, `samples.capture`, `samples.delete`;
* fayllar: `data/samples/<toifa>/<toifa>_NNN.jpg`; ZIP: `/api/training/samples.zip`.

**Muhim:** rasm yuklashning o‘zi modelni o‘zgartirmaydi. Eng qimmatlisi — **kameradan
o‘zidan** olingan kadrlar: internetdan yuklangan oq fonli surat modelga “maus nima
ekanligini” o‘rgatadi, lekin sizning xonangiz, yorug‘ligingiz va qo‘lda tutilgan holatni
ko‘rsatmaydi. Shuning uchun har 10 ta namunadan kamida 6–7 tasi kameradan bo‘lsin.

Zanjir:

```bat
.venv\Scripts\python.exe scripts\train_yolo.py --init dataset
.venv\Scripts\python.exe scripts\train_yolo.py --import-samples dataset
REM ixtiyoriy: ochiq YOLO-DeskDataset'dan monitor/klaviatura/maus kadrlari
.venv\Scripts\python.exe scripts\import_public_dataset.py --max-per-class 60
REM oldindan belgilash (model qutilarni taklif qiladi, papka nomi sinfni to‘g‘rilaydi)
.venv\Scripts\python.exe scripts\autolabel_dataset.py --dataset dataset
REM dataset\preview\train dagi rasmlarni ko‘ring, labelImg/CVAT bilan tuzating
.venv\Scripts\python.exe scripts\train_yolo.py --split-val 0.2
.venv\Scripts\python.exe scripts\train_yolo.py --check dataset\data.yaml
.venv\Scripts\python.exe scripts\train_yolo.py --data dataset\data.yaml --run --epochs 40 --device cpu
```

`import_public_dataset.py` — [hhh134/YOLO-DeskDataset](https://huggingface.co/datasets/hhh134/YOLO-DeskDataset)
(1 646 kadr, 80 sinf) ichidan faqat loyiha toifasiga **aynan mos** 8 sinfni oladi
(`laptop`, `mouse`, `keyboard`, `monitor`, `smartphone`, `tablet`, `printer`, `speaker`),
sinf raqamlarini `data.yaml` tartibiga o‘giradi va `dataset/images|labels/train` ga qo‘shadi.
`data.yaml`, mavjud kadrlar va `data/models` o‘zgarmaydi; qayta yugurtirish dublikat yaratmaydi.
To‘plamda `person` sinfi **yo‘q** — shuning uchun `train_yolo.py` datasetda odam bo‘lmasa
modelni avtomatik almashtirmaydi (faqat `--deploy` bilan majburlash mumkin). Batafsil:
`docs/yolo_training.md`, 2.1.

`autolabel_dataset.py` — o‘qitishni tezlashtiradigan yordamchi, tayyor yechim emas:
COCO modeli faqat bilgan sinflarini taklif qiladi, qolganlarini (printer, UPS, LAN kabel…)
`LabelImg`/CVAT bilan qo‘lda chizish kerak. Geometriya qoidasi (`plausible_box`) bo‘yicha
shubhali qutilar datasetga yozilmaydi, `dataset\preview\` ga esa model (yashil) va
tuzatilgan (sariq) qutilar chizib qo‘yiladi.

`--check` talabi: **100+** belgilangan `train` va **50+** `val` kadr. Shundan kam bo‘lsa
o‘qitish ma’nosiz — model o‘z sinflarini yomonlashtirishi mumkin. Shu sababli `best.pt`
faqat `MIN_DEPLOY_LABELS = 100` tadan keyin avtomatik `data\models\equipment_best.pt` ga
nusxalanadi; ataylab majburlash uchun `--deploy` bayrog‘i bor.

Yangi `best.pt` ni tizimga olish: `/settings` → **YOLO modelini qayta yuklash**.
Batafsil: `docs/yoriqnoma_uz.md` 9.14 va 11.2, `docs/yolo_training.md`.

---

## 12. Google Sheets (hisobot oynasi)

Tizim lokal bazaga yozadi, keyin qo‘shimcha ravishda Sheets’ga nusxalaydi. Sheets
vaqtincha ishlamasa — hech narsa yo‘qolmaydi, keyingi urinishda yuboriladi.

### 12.1. Ulash: `/sheets` ustaxonasi (3 qadam)

Interfeysdagi eng oson yo‘l — chap menyu → **Boshqaruv → Google Sheets** (faqat admin).

1. **Google Cloud** — loyiha → *Google Sheets API* → Enable → *Service account*
   yaratish → *Keys → Add key → JSON*. Kalit fayl kompyuterga yuklanadi.
2. **Kalitni yuklash** — `/sheets` 2-qadam: JSON faylni tanlang (yoki matnini
   joylashtiring) → *Kalitni saqlash*. Fayl `secrets/google_service_account.json`
   bo‘lib 0600 ruxsat bilan saqlanadi, `.env` avtomatik yangilanadi. Sahifada
   **client_email** va **Nusxalash** tugmasi paydo bo‘ladi.
3. **Jadvalni ulash** — jadval yaratib, `client_email` ni *Editor* qilib qo‘shing,
   so‘ng 3-qadamda havola yoki ID’ni kiriting (interval 15–86400 s).

Keyin: **Sinovdan o‘tkazish** (ulanish + varaqlar ro‘yxati), **Varaqlarni tayyorlash**
(4 varaq sarlavhalari bilan), **Hozir sinxronlash**, **avtomatik yoqish/o‘chirish** va
**Kalitni o‘chirish** (ulanishni uzish) tugmalari. Xatolar o‘zbekcha izoh bilan
chiqadi: `403` → jadval bo‘lishilmagan, `404` → ID xato, `invalid_grant` → kalit eskirgan.

### 12.2. Qo‘lda sozlash (`.env`)

```ini
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SPREADSHEET_ID=1AbC...jadval_ID
GOOGLE_SERVICE_ACCOUNT_FILE=secrets/google_service_account.json
GOOGLE_SHEETS_SYNC_INTERVAL_SECONDS=120
GOOGLE_SHEETS_MAX_RETRIES=4
```

Holat va sinxronlash: `/reports#sheets`, `/api/sheets/status`, CLI:

```bat
.venv\Scripts\python.exe scripts\sync_google_sheets.py
.venv\Scripts\python.exe scripts\sync_google_sheets.py --status
```

Bitta buyruq bilan ulashni sozlash (papka yaratadi, kalitni tekshiradi, havoladan ID
oladi, `.env` ni yangilaydi va sinaydi):

```bat
.venv\Scripts\python.exe scripts\connect_google_sheets.py ^
  --url "https://docs.google.com/spreadsheets/d/<ID>/edit?gid=0#gid=0" ^
  --key "%USERPROFILE%\Downloads\<kalit>.json" --prepare
```

`secrets/` ichiga **istalgan nomdagi** service-account JSON tashlansa ham topiladi:
Google yuklab bergan nom (`loyiha-1a2b3c.json`) muhim emas — sozlangan yo‘l mavjud
bo‘lmasa, papkadagi haqiqiy kalit avtomatik ishlatiladi.

Varaqlar (avtomatik yaratiladi, sarlavhalar yoziladi):

| Varaq | Mazmun |
| --- | --- |
| `Employees` | Xodim ID, ism, familiya, bo‘lim, holat (Faol/Faolsiz) |
| `Equipment` | Aktiv kodi, toifa, brend, model, seriya, joylashuv, holat |
| `MovementEvents` | Event ID, vaqt, xodim, hodisa turi, jihoz, yo‘nalish, kamera, holat, izoh |
| `InventorySummary` | Toifa bo‘yicha qoldiq va farq (har sinxronizatsiyada yangilanadi) |

`MovementEvents` **Event ID** bo‘yicha dedupe qilinadi: varaqda mavjud ID qayta
yozilmaydi. Har bir jadval yozuvi `_retry` bilan eksponensial qayta uriniladi
(4 martagacha). Muvaffaqiyatsiz hodisalar `FAILED` bo‘lib, keyingi sinxronizatsiyada
qayta yuboriladi. Fonda scheduler har `GOOGLE_SHEETS_SYNC_INTERVAL_SECONDS` da
ishlaydi (yoqilgan bo‘lsa).

---

## 13. Hisobotlar, yuklab olish va eksport

* `/reports` — davr, kamera, toifa, hodisa turi, holat, xodim bo‘yicha filtrlar;
  turi/toifa/xodim/holat kesimida yig‘indi; balanslar jadvali.
* `GET /reports/export.csv` — o‘sha filtrlar bilan CSV. Fayl UTF-8 BOM bilan
  yuboriladi (Excel’da o‘zbekcha matn buzilmaydi). Ustunlar: Event ID, vaqt,
  hodisa turi, kamera, xodim kodi/F.I.Sh., jihoz toifasi, Asset ID, yo‘nalish,
  holat, aniqlik, yuz o‘xshashligi, bog‘lanish ishonchi, izoh.
* **`/downloads` (“Yuklab olish”)** — 6 turdagi ma’lumotni **Excel (.xlsx)** yoki
  **CSV** qilib beradi, sana oralig‘i bilan (jadvalga qarang).

| Kalit | Mazmuni | Davr filtri |
| --- | --- | --- |
| `inventory` | Toifalar bo‘yicha qoldiq, kutilayotgan harakat, farq | yo‘q |
| `ledger` | Inventar jurnali (append-only) | bor |
| `events` | Kamera hodisalari, ishonch ballari, dalil yo‘li | bor |
| `employees` | Xodimlar va shablonlar soni | yo‘q |
| `assets` | Jihozlar ro‘yxati | yo‘q |
| `audit` | Audit jurnali (faqat admin) | yo‘q |

* Eksport manzillari: `/downloads/<kalit>.xlsx`, `/downloads/<kalit>.csv?start=…&end=…`,
  `/downloads/all.xlsx` (6 varaqli bitta fayl). Excel’da sarlavha qotirilgan,
  avtofiltr va ustun kengliklari sozlangan; CSV — UTF-8 BOM bilan. Bir faylda
  ko‘pi bilan 20 000 qator (kattaroq tarix uchun davrni qisqartiring), dalil
  rasmlari faylga qo‘shilmaydi. Har bir yuklab olish `export.download` audit
  yozuvi bilan qayd etiladi.
* `GET /api/events?limit=50` — integratsiya uchun JSON (biometrik ma’lumot yo‘q).
* `GET /api/sheets/status` — Sheets holati va navbatdagi hodisalar soni.

---

## 14. Dalillar (evidence)

* Har bir person/jihoz hodisasi uchun JPEG kadr: `data/evidence/YYYY-MM-DD/`.
* `EVIDENCE_ENABLED=false` — saqlanmaydi; `EVIDENCE_JPEG_QUALITY` — sifat.
* `EVIDENCE_RETENTION_DAYS` (standart 30) — har soatda eski fayllar o‘chiriladi
  (janitor thread).
* Ko‘rish faqat `/api/events/{public_id}/evidence` orqali, operator+; yo‘l
  traversal’dan himoyalangan. Tizim hodisalarida rasm bo‘lmaydi (404 — normal).

---

## 15. Sozlamalar sahifasi

`/settings` (admin):

* Oq ro‘yxatdagi `.env` qiymatlarini tahrirlash (atomik yozish + qayta yuklash).
* “YOLO modelini qayta yuklash” — yangi `best.pt` ni qo‘llash.
* “Galereyani yangilash” — yuz shablonlarini qayta indekslash.
* Foydalanuvchilar: yaratish, parolni almashtirish, bloklash/faollashtirish.
* Audit jurnali (oxirgi 40 amal) va `SyncLog` yozuvlari.
* Konfiguratsiya ogohlantirishlari.

---

## 16. CLI skriptlar

Hammasi loyiha ildizidan ishga tushiriladi:

```bat
.venv\Scripts\python.exe scripts\test_camera.py --code CAM-01
.venv\Scripts\python.exe scripts\test_camera.py --type usb --source 0 --seconds 5
.venv\Scripts\python.exe scripts\test_camera.py --type simulation --seconds 6
.venv\Scripts\python.exe scripts\register_employee.py --list
.venv\Scripts\python.exe scripts\enroll_employees.py --folder "C:\texniklar" --department "Texniklar"
.venv\Scripts\python.exe scripts\enroll_employees.py --check
.venv\Scripts\python.exe scripts\train_yolo.py --init dataset
.venv\Scripts\python.exe scripts\train_yolo.py --import-samples dataset
.venv\Scripts\python.exe scripts\import_public_dataset.py --max-per-class 60
.venv\Scripts\python.exe scripts\autolabel_dataset.py --dataset dataset
.venv\Scripts\python.exe scripts\train_yolo.py --split-val 0.2
.venv\Scripts\python.exe scripts\train_yolo.py --check dataset\data.yaml
.venv\Scripts\python.exe scripts\train_yolo.py --data dataset\data.yaml --epochs 80
.venv\Scripts\python.exe scripts\sync_google_sheets.py
.venv\Scripts\python.exe scripts\connect_google_sheets.py --url "<jadval havolasi>" --key "<kalit>.json" --prepare
```

`test_camera.py` modelni ishga tushirmaydi — faqat manba, kadr o‘lchami va FPS ni
tekshiradi, kadrlarni `data/debug/` ga saqlaydi (chiziqni belgilash uchun qulay).

---

## 17. Testlar

```bat
.venv\Scripts\python.exe -m pytest
```

Hozirda **276 ta test** (barchasi o‘tadi). `pytest.ini` faqat `tests/` ni yig‘adi;
har bir test vaqtinchalik SQLite bazasida ishlaydi (haqiqiy `data/inventory.db` ga
tegilmaydi). Qamrov:

* inventar: INITIAL/kirim/chiqim/qaytarish/transfer/tuzatish, PENDING nomzodlar,
  tasdiqlash/rad etish, balans va farq formulalari;
* hodisalar: dedupe kaliti, cooldown, review, JSON;
* bog‘lanish: ball formulasi, sifat darajalari, review bayrog‘i;
* trekking: ByteTrack-o‘xshash ID barqarorligi, chiziq kesishish (histerezis,
  cooldown, yo‘nalishni teskari qilish), nuqta-poligon;
* yuz: kosinus o‘xshashligi, chegara va marja, noma’lum shaxs;
* shablon rasmlari: dublikat filtr, 15 ta chegara, fayl nomi bo‘shliqni qayta
  ishlatmasligi, o‘chirish auditi; kamera kadridan rasm olish (kadr -> yuz ->
  shablon) va xato holatlari; yangi texnika toifasi (validatsiya, audit, rol);
* Sheets: upsert/dedupe, retry, FAILED holati (soxta gspread bilan);
* Sheets ulash: kalit validatsiyasi, jadval URL/ID ajratish, sinov natijalari,
  varaqlarni yaratish, huquqlar;
* eksport: XLSX/CSV tuzilishi, BOM, 6 varaqli `all.xlsx`, rol cheklovi, audit;
* kamera: manba yo‘qligi, OFFLINE xabari, simulyatsiya, JPEG;
* HTTP: 12 sahifa, rollar, CSRF, login/logout, CSV BOM, dalil 404, JSON API;
* tekshirish: rad etishda sabab majburiyligi, eski `asset_id` xatosi 500 bermasligi;
* namuna rasmlar: yuklash/nusxa filtr/o‘chirish/ZIP/kameradan kadr, 10 ta chegarasi va admin cheklovi;
* aniqlik: shakl/maydon qoidalari (`plausible_box`), telefon ↔ maus chalkashuvini yechish,
  yaqin plandagi maus 20 % chegarasi;
* pre-label: sinf nomlarini o‘girish, papka bo‘yicha to‘g‘rilash, YOLO `.txt` matematikasi,
  prodakshn modelini almashtirish himoyasi (`MIN_DEPLOY_LABELS`).

---

## 18. Zaxira nusxa va tiklash

Server to‘xtatilganda:

```bat
copy data\inventory.db  D:\backup\inventory-2026-10-28.db
xcopy /E /I data\evidence D:\backup\evidence
copy .env D:\backup\.env
xcopy /E /I secrets D:\backup\secrets
```

Tiklash: serverni to‘xtatib, fayllarni joyiga qaytarish. SQLite WAL rejimida
ishlaydi (`-wal`, `-shm` fayllari paydo bo‘ladi) — nusxa olishdan oldin serverni
to‘xtatish eng ishonchli usul.

Kunlik zaxira uchun Windows Task Scheduler / cron’ga yuqoridagi buyruqlarni qo‘ying.

---

## 19. Muammolarni bartaraf etish

| Alomat | Sabab | Yechim |
| --- | --- | --- |
| `Maxsus YOLO modeli topilmadi` ogohlantirishi | `data/models/equipment_best.pt` yo‘q | Bu xato emas: demo uchun `yolov8s.pt` avtomatik ishlatiladi; ishlab chiqarish uchun [docs/yolo_training.md](docs/yolo_training.md) |
| Yuz tanish “mavjud emas” | `insightface`/`onnxruntime` o‘rnatilmagan yoki model yuklanmagan | `pip install -r requirements-ai.txt`; **serverni qayta ishga tushiring**; logni tekshiring; Sozlamalar → galereyani yangilash |
| Kamera `OFFLINE` | RTSP noto‘g‘ri, parol, tarmoq, kodek | `scripts/test_camera.py --source ...` bilan tekshiring; H.264 sub-stream ishlatib ko‘ring |
| USB kamera: `Camera index out of range` | Manba maydonidagi indeks mavjud emas (masalan `1`, kompyuterda esa faqat `0` bor) | `python -c "import cv2; print(cv2.VideoCapture(0).read()[0])"` bilan mavjud indeksni toping va manbani o‘zgartiring |
| `Ulanib bo‘lmadi` takrorlanadi | Manba yo‘q | Backoff 5→60 s; manbani tuzating, keyin “Yangilash” |
| Port band | Boshqa jarayon | `run.py --port 8011` yoki jarayonni to‘xtatish |
| `database is locked` | Ikki server bir bazada | Bitta server ishlating; AI/CLI skriptlari qisqa tranzaksiyalar bilan ishlaydi |
| Sheets `FAILED` | Kalit/ID noto‘g‘ri, jadval ulashilmagan | Service account email’ini Editor qiling; `--status` bilan tekshiring |
| `Kalit fayli topilmadi` | `.env` dagi yo‘l mavjud emas (fayl ko‘chirilgan/o‘chirilgan) | JSON faylni `secrets/` ichiga tashlang (nomi muhim emas) yoki `scripts\connect_google_sheets.py --key ...` bilan qayta yuklang |
| `Jadval ID shubhali` | ID qisqa yoki faqat raqamlar (44 belgili ID emas) | Brauzer manzil satridan havolani to‘liq nusxalang |
| Login ishlamaydi (`production`) | `https_only` cookie | HTTPS orqali oching yoki `APP_ENV=development` qiling |
| Past FPS / issiq CPU | CPU inference | `PROCESSING_FPS` ni 5–8 ga tushiring, `YOLO_IMAGE_SIZE=480`, `FACE_RECOGNITION_INTERVAL` ni oshiring yoki GPU |
| Tushunarsiz xato sahifasi | Kutilmagan istisno | `logs/app.log` (traceback) |

---

## 20. Custom YOLO modelini o‘qitish

Standart `yolov8s.pt` (COCO) faqat `person`, `laptop`, `tv`, `cell phone` kabi
sinflarni biladi; `printer`, `ups`, `ip_phone`, `lan_tester`, `lan_cable`,
`mini_camera` kabi ish joyi jihozlari uchun custom model kerak. To‘liq yo‘riqnoma:
**[docs/yolo_training.md](docs/yolo_training.md)** — `CLASS_NAMES` ro‘yxati 27 sinfga
kengaytirilgan va toifa kodlari bilan bir xil nomlangan.

Toifalar (27 ta) startda avtomatik seed qilinadi. Har bir toifa bir necha YOLO
sinf nomiga (`yolo_labels`) bog‘langan va **bir sinf faqat bitta toifaga** tegishli
bo‘ladi: agar ikkita toifa bir xil nomga da’vo qilsa, eng yuqori `sort_order` li
toifa tanlanadi (masalan `tv` → Televizor, `monitor` dan olib tashlanadi).

### Aniqlikni oshirish (o‘qitishsiz ham)

* **Dublikat bostirish** — bitta obyekt ikki sinf sifatida topilsa (`tv` +
  `monitor`), `YOLO_DEDUPE_IOU` (standart `0.6`) bo‘yicha faqat ishonchlirog‘i
  qoladi: jurnalga ikki marta yozilmaydi.
* **Trek bo‘yicha ovoz berish** — kadrma-kadr sinf almashib tursa, trekning
  ko‘pchilik sinfi ishlatiladi (masalan 3 kadrda 2 marta `monitor`).
* **Toifa chegarasi** — har toifaning `minimal ishonch` qiymati modeldan
  mustaqil filtr vazifasini bajaradi; endi qat’iy **pol** bor: telefon/planshet 0.75,
  maus/klaviatura/kabel 0.70, qolgan jihozlar 0.60 (11.2).
* **Shakl va chalkashuv qoidalari** — nisbat/maydon filtri va “telefon ↔ maus”
  yechish; qo‘lda **tik** tutilgan maus ham taniladi (nisbat 0.45–2.60, maydon 20 %,
  telefon uchun 10 %) — bular model o‘qitilmaguncha ham yolg‘on hodisani kamaytiradi.
* **Namuna rasmlar** — `/training` da yig‘ilgan 10–15 rasm keyingi o‘qitish uchun
  asos bo‘ladi (11.4). Modelni almashtirmaguncha aniqlik oxirgi darajada qoladi.
* **Eng yaxshi yuz mosligi saqlanadi** — keyingi xira kadr ishonchli
  identifikatsiyani pasaytirmaydi.

Qisqa oqim:

```bat
.venv\Scripts\python.exe scripts\train_yolo.py --init dataset
REM panelda /training orqali namunalar yig‘ilsa: ularni datasetga nusxalash
.venv\Scripts\python.exe scripts\train_yolo.py --import-samples dataset
REM qolgan kadrlarni dataset\images\train|val ga yuklang va labelImg/CVAT bilan belgilang
.venv\Scripts\python.exe scripts\train_yolo.py --check dataset\data.yaml
.venv\Scripts\python.exe scripts\train_yolo.py --data dataset\data.yaml --run --epochs 80 --device 0
```

`best.pt` avtomatik `data/models/equipment_best.pt` ga nusxalanadi (faqat `labels/train` da
100+ belgilangan kadr bo‘lsa; aks holda model almashtirilmaydi). Sozlamalar →
“YOLO modelini qayta yuklash”. Sinflar `Jihozlar` sahifasida toifalarga
(`yolo_labels`) bog‘langan — yangi sinf qo‘shsangiz, shu yerda moslashtiring.

Yangi toifani paneldan ham qo‘shish mumkin: `Jihozlar` → “Yangi texnika toifasi”.
Kod (masalan `drill`), o‘zbekcha nomi, YOLO sinf nomlari (vergul bilan) va
minimal ishonch kiritiladi. Mavjud toifalar o‘zgarmaydi; yangi qator ro‘yxat
oxiriga qo‘shiladi va harakat `category.create` bo‘lib audit jurnaliga yoziladi.
Model bu sinfni tanimasa, jihoz aniqlanmaydi — avval YOLO qayta o‘qitiladi,
keyin sinf nomi shu formaga kiritiladi (kod o‘zgartirish kerak emas).

---

## 21. Halol hisobot: nima tekshirilgan, nimaga real jihoz kerak

**Ushbu loyihada yozilgan va tekshirilgan (avtomatik testlar + real server):**

* Barcha 10 sahifa, login/logout, rollar, CSRF, 404/403, audit yozuvlari.
* Inventar jurnali va balans formulalari; PENDING → CONFIRM/REJECT oqimi
  (rad etilgan qatorlar balansga ta’sir qilmasligi test bilan tasdiqlangan).
* Hodisa dedupe/cooldown, bog‘lanish ballari, chiziq kesishish mantiqi.
* Yuz mosligi chegarasi/marjasi (sof matematika + servis testlari).
* Sheets upsert/dedupe/retry (soxta Google API bilan).
* Kamera servisining uzilish/qayta ulanish xatti-harakati va simulyatsiya manbasi.
* **Simulyatsiya oqimi uchdan-uchiga:** kamera `ONLINE`, kadrlar oqimi, chiziq
  kesishishi, `EQUIPMENT_BROUGHT_IN` + `UNKNOWN_PERSON_ENTERED` hodisalari, dalil
  JPEG’lari (HTTP 200 `image/jpeg`), tasdiqlanmagan jurnal nomzodi (`pending_in=1`),
  CSV eksporti, `/healthz`.
* Server `run.py` bilan Windows’da ko‘tarilishi; YOLO modeli yo‘qligida ham
  ishlashi (ogohlantirish bilan).

**Real jihoz/kalit talab qiladigan (kod yozilgan, lekin bu muhitda sinab
bo‘lmaydigan) qismlar:**

* RTSP/USB kamera bilan real kadr olish va FPS — faqat real kamera bilan tekshiriladi.
* Custom YOLO aniqlik ko‘rsatkichlari — o‘qitilgan model va sayt kadrlari kerak
  (repozitoriyda model yo‘q). Umumiy `yolov8s.pt` (COCO, 80 sinf) fallback
  modeli esa yuklab olinib, sinov rasmida odamlarni aniqlashi tekshirildi.
* InsightFace bilan yuz tanish **zanjiri** sinov rasmlarida tekshirildi: rasm
  yuklash → `buffalo_l` embedding (512-o‘lchov) → bazaga saqlash → galereyaga
  yuklash → tanilgan yuz `1.00` o‘xshashlik bilan mos keldi, boshqa shaxs esa
  `0.03` bilan “Noma’lum shaxs” bo‘ldi. Sayt kadrlaridagi real aniqlik esa
  xodimlarning haqiqiy rasmlari bilan kalibrlashni talab qiladi.
* Google Sheets’ga real yozish — service account va jadval kerak.
* GPU (CUDA) tezligi — mos GPU kerak.
* Uzoq muddatli barqarorlik (kunlar/oylar) va ko‘p kamerali yuklama.

**Ma’lum cheklovlar (yashirilmaydi):**

* Trek ID’lar **har kamera ichida**; kameralar orasida shaxsni kuzatish yo‘q.
* Jihoz **toifa darajasida** hisoblanadi (aniq inventar raqami emas, agar operator
  bog‘lamasa); `TRANSFER` sonni o‘zgartirmaydi.
* Faqat chiziq kesishish ishlatiladi (hudud/zonaga kirish emas).
* SQLite bitta server uchun; katta yuklamada PostgreSQL + alohida worker tavsiya etiladi.
* Notifications (Telegram/SMS) yo‘q — hisobot va dashboard orqali kuzatiladi.
* `EQUIPMENT_RETURNED` turi mavjud, lekin avtomatik oqimda jihoz qaytarish
  hodisasi alohida generatsiya qilinmaydi; qaytarish inventar amali orqali kiritiladi.

---

## 22. Loyiha tuzilmasi

```
face-date/
├── app/
│   ├── main.py                 # FastAPI app, middleware, lifespan
│   ├── config.py               # .env -> Settings, ogohlantirishlar
│   ├── database.py             # engine, sessiya, PRAGMA, init/reset
│   ├── models/                 # SQLAlchemy modellari (event, ledger, ...)
│   ├── services/               # kamera, pipeline, YOLO, yuz, inventar, sheets, export_service
│   ├── api/                    # routerlar (pages, auth, cameras, downloads, sheets_setup, ...)
│   ├── ui/templates/           # Jinja2 shablonlar (16 ta)
│   └── ui/static/              # app.css, app.js
├── scripts/                    # register_employee, enroll_employees, train_yolo, test_camera, sync, connect_sheets
├── tests/                      # pytest to‘plami + conftest
├── docs/
│   ├── yolo_training.md        # custom model yo‘riqnomasi
│   ├── YORIQNOMA-face-date.docx  # to‘liq foydalanuvchi qo‘llanmasi (Word, 38 bet)
│   └── yoriqnoma_uz.md         # qo‘llanmaning manbasi (Markdown)
├── data/                       # baza, evidence, samples, models (git’ga kirmaydi)
├── logs/                       # app.log
├── secrets/                    # service account JSON (git’ga kirmaydi)
├── .env.example
├── requirements.txt            # asosiy
├── requirements-ai.txt         # ultralytics + insightface + onnxruntime
├── run.bat / run.sh / run.py
└── pytest.ini
```

---

## 23. Xavfsizlik bo‘yicha minimal talablar

* `APP_SECRET_KEY` ni uzun tasodifiy qiymatga almashtiring:
  `py -c "import secrets; print(secrets.token_urlsafe(48))"`.
* Bootstrap admin parolini birinchi kirishdayoq o‘zgartiring; ortiqcha hisoblarni
  bloklang.
* `production` rejimida HTTPS (nginx/Caddy reverse proxy) ishlating — aks holda
  sessiya cookie yuborilmaydi.
* `secrets/`, `.env`, `data/` papkalarini zaxiraga oling, lekin git’ga qo‘shmang
  (`.gitignore` allaqachon istisno qiladi).
* Tizimni faqat ichki tarmoqqa oching; tashqi kirish kerak bo‘lsa VPN yoki
  autentifikatsiyali proxy qo‘llang.

---

## 24. To‘liq foydalanuvchi qo‘llanmasi (Word)

**`docs/YORIQNOMA-face-date.docx`** — to‘liq qo‘llanma (38 bet): tushunchalar, o‘rnatish,
`.env`, run qilish, 13 sahifa bo‘yicha amaliy qadamlar, Google Sheets’ni 3 qadamda
ulash, Excel/CSV yuklab olish, texniklarni rasm bilan o‘qitish (panel + kamera + CLI),
aniqlik chegaralari, `/training` namunalari va qayta o‘qitish, inventar qoidalari,
nosozliklar jadvali, zaxira nusxa, FAQ va nazorat ro‘yxati. Yonida PDF nusxasi ham bor.

Manba matn — `docs/yoriqnoma_uz.md`. Undan Word faylini qayta yaratish:

```bat
.venv\Scripts\python.exe scripts\build_manual.py
```

Skript `python-docx` ishlatadi (`.venv` ichida o‘rnatilgan). Matnni tahrirlab,
buyruqni qayta ishga tushirsangiz — `.docx` yangilanadi.


_______________________________________________________________________________________________________________________________    YOLO AI

YOLO versiyasi va dvigatel
Komponent	Versiya / qiymat
yolov8s.pt
Ultralytics (YOLOv8)	8.3.40
PyTorch	2.14.1+cpu (CUDA yoqilmagan)
TorchVision	0.29.1+cpu
OpenCV	4.10.0
NumPy	2.2.6
InsightFace (yuz, YOLO’ga aloqasi yo‘q)	2.1 + onnxruntime 1.20.1 (buffalo_l)


Deteksiya sozlamalari (.env)
Sozlama	Qiymat	Ma’nosi
YOLO_CONFIDENCE	0.35	modelning umumiy chegarasi (pastki filtr — 0.25 dan pastga tushmaydi)
YOLO_IMAGE_SIZE	640	kirish o‘lchami (CPU uchun 314 ms/kadr ≈ 3.2 fps; 960 = 656 ms)
YOLO_DEVICE	cpu	GPU yo‘q
YOLO_TRACKER	bytetrack.yaml	obyekt kuzatuvi (trek id)
YOLO_DEDUPE_IOU	0.6	bir-birini takrorlovchi qutilarni olib tashlash
PROCESSING_FPS	10	kamera oqimidan sekundiga qayta ishlanadigan kadr


