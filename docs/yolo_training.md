# Custom YOLO modelini o‘qitish (jihozlar)

Bu yo‘riqnoma tizim ishlatadigan **o‘z modelingizni** (masalan
`data/models/equipment_best.pt`) tayyorlash uchun. Model standart COCO
`yolov8s.pt` dan farqli ravishda ofis jihozlarini (`printer`, `ups`,
`computer_case` va h.k.) taniydi.

Tizim modelni topmasa ham ishlaydi: `YOLO_FALLBACK_MODEL` bilan ko‘tariladi
va logda `Maxsus o‘qitilgan model topilmadi` ogohlantirishi turadi — bu xato
emas, shunchaki umumiy model ishlayotganini bildiradi.
Bu demo uchun yetarli, ishlab chiqarish uchun emas.

---

## 0. YOLO26 rejimi (2026-11-03)

Hozir `.env` da `YOLO_FALLBACK_MODEL=data/models/yolo26x.pt` turadi
(Ultralytics **8.4.199**, COCO 80 sinf; YOLO26 ni 8.4.x dan pastki versiya
umuman yuklay olmaydi). `data/models/equipment_best.pt` paydo bo‘lsa, u
automatik ustun turadi — kod o‘zgarmaydi.

O‘lchov: `dataset/images/val` (83 yorliqlangan kadr), `conf=0.35`,
`imgsz=640`, CPU (Core i5-9500, GPU yo‘q):

| Model | ms/kadr | keyboard F1 | laptop F1 | monitor F1 | mouse F1 |
| --- | --- | --- | --- | --- | --- |
| yolov8s (oldingi) | 114 | 0.79 | 0.67 | 0.61 | 0.73 |
| yolo26s | 136 | 0.78 | 0.72 | 0.71 | 0.83 |
| yolo26m | 357 | 0.78 | 0.81 | 0.75 | 0.84 |
| **yolo26x (joriy)** | **733** | **0.87** | **0.80** | **0.84** | **0.88** |

Ilovaning o‘z yo‘li (`YoloService.detect`, ByteTrack bilan) yolo26x da
**780–930 ms/kadr** beradi, ya’ni ~1.2 fps. Kamera navbati to‘la bo‘lsa
`camera_service` eng eski kadrni tashlaydi — ilova bloklanmaydi, lekin
vaqt birligida kamroq kadr tahlil qilinadi. Tezlik muhim bo‘lsa, bir qator
o‘zgartirish yetarli (ikkala model `data/models` da tayyor):

```bat
YOLO_FALLBACK_MODEL=C:\Users\Admin\Desktop\face-date\data\models\yolo26m.pt
```

**GPU/FP16:** bu kompyuterda NVIDIA GPU yo‘q (Intel UHD 630, `torch
2.14.1+cpu`), shuning uchun `YOLO_DEVICE=cpu` — CUDA va FP16 bu yerda
qo‘llanmaydi. GPU qo‘shilganda: CUDA buildidagi torch o‘rnatiladi,
`YOLO_DEVICE=0` qilinadi (FP16 ni faqat CUDA da tekshirish mumkin).

**Orqaga qaytarish:** `YOLO_FALLBACK_MODEL=yolov8s.pt` va qayta ishga tushirish.

**Tekshiruv (2026-11-03):** to‘liq zanjir alohida bazada sinaldi — dalil
kadrlaridan yasalgan video `file` turidagi kamera sifatida ulanib, jonli
server `YOLO model yuklanmoqda: ...yolo26x.pt` dedi, ByteTrack trek ID
berdi va `EQUIPMENT_PICKED_UP` (monitor, conf 0.63, trek 2/5) event’i
dalil rasmi bilan bazaga yozildi. Skript: `logs/yolo26_probe/sim_e2e.py`.
Model **birinchi kadr kelganda** yuklanadi (lazy) — kamera o‘chiq bo‘lsa,
logda `YOLO model yuklanmoqda` qatori kechikadi, bu xato emas.

---

## 1. Sinf ro‘yxati va toifalar

`scripts/train_yolo.py` dagi `CLASS_NAMES` tartibi **qat’iy** — `class_id`
shu tartibga mos bo‘lishi shart. Joriy ro‘yxat (27 sinf):

| ID | Sinf nomi | Bazadagi toifa (`Jihozlar`) |
| --- | --- | --- |
| 0 | `person` | Shaxs |
| 1 | `desktop_pc` | Stol kompyuteri |
| 2 | `monitor` | Monitor |
| 3 | `all_in_one_pc` | Monoblok |
| 4 | `laptop` | Noutbuk |
| 5 | `keyboard` | Klaviatura |
| 6 | `mouse` | Sichqoncha |
| 7 | `computer_case` | Tizim bloki |
| 8 | `printer` | Printer |
| 9 | `scanner` | Skaner |
| 10 | `ups` | UPS |
| 11 | `speaker` | Dinamik |
| 12 | `network_device` | Tarmoq uskunasi |
| 13 | `other_equipment` | Boshqa jihoz |
| 14 | `tv` | Televizor |
| 15 | `projector` | Proyektor |
| 16 | `ip_phone` | IP telefon |
| 17 | `landline_phone` | Statsionar telefon |
| 18 | `smartphone` | Smartfon |
| 19 | `tablet` | Planshet |
| 20 | `camera` | Kamera (IP/CCTV) |
| 21 | `mini_camera` | Mini IP kamera |
| 22 | `lan_tester` | LAN tester |
| 23 | `lan_cable` | LAN kabel |
| 24 | `external_hdd` | Tashqi disk |
| 25 | `flash_drive` | USB flesh |
| 26 | `server` | Server |

Sinf nomlarini toifa kodlaridan **o‘zgartirmang**: toifa `yolo_labels`
maydonida aynan shu nomlar turadi, shuning uchun `best.pt` ni almashtirgach
hech narsa sozlash kerak bo‘lmaydi.

**Bir sinf — bitta toifa.** Ikkita toifa bir xil sinf nomiga da’vo qilsa
(masalan `tv` ham `Monitor`, ham `Televizor` da bo‘lsa), startda
`resolve_label_ownership()` eng yuqori `sort_order` li toifani tanlaydi va
boshqasidan bu nomni olib tashlaydi. Shuning uchun sinf nomlari takrorlanmasin.

Toifalar `Jihozlar` sahifasida tahrirlanadi: har bir toifa bir yoki bir nechta
YOLO sinf nomiga (`yolo_labels`, vergul bilan) bog‘lanadi. Modelda sinf nomi
boshqacha bo‘lsa (masalan `notebook`), toifaga shu nomni qo‘shish kifoya —
qayta o‘qitish shart emas.

`person` sinfi **majburiy**: u orqali odam trek’lari va chiziq kesishishi
hisoblanadi. Uni boshqa sinf bilan almashtirmang.

### COCO (o‘qitilmagan) rejimda nima ishlaydi

`yolov8s.pt` faqat COCO sinflarini biladi. Hozirgi bog‘lanishlar:

| COCO sinfi | Toifa | Izoh |
| --- | --- | --- |
| `person` | Shaxs | to‘liq ishlaydi |
| `laptop` | Noutbuk | to‘liq ishlaydi |
| `keyboard`, `mouse` | Klaviatura, Sichqoncha | faqat yaqin masofada |
| `tv` | **Monitor** | COCO ekranni `tv` deb ataydi — dalil kadrlarida bu monitor edi |
| `cell phone` | Smartfon | o‘chirilgan (sichqoncha/LAN tester/IP telefonni ham shunday deb topardi) |
| `remote`, `book` | — | bog‘lanmagan (e’tiborsiz qoldiriladi) |

IP telefon, statsionar telefon, LAN tester, LAN kabel, kamera, mini kamera,
proyektor, printer, UPS, server va tizim bloki **faqat o‘qitilgan modelda**
toplanadi — COCO modelida bu sinflar umuman yo‘q. Umumiy holat: 27 toifadan
**6 tasi** COCO yorliqlari bilan qoplanadi, qolgan 21 tasi uchun o‘qitilgan
model shart.

### 1.1. O‘lchangan holat (2026-11-02)

O‘lchovlar: `dataset/images/val` (79 yorliqlangan kadr, YOLO-DeskDataset +
ofis kadrlari), IoU 0.5, `yolov8s.pt`, CPU.

| Toifa | COCO sinfi | eng yaxshi F1 chegarasi | P | R |
| --- | --- | --- | --- | --- |
| keyboard | `keyboard` | 0.50 | 0.88 | 0.77 |
| laptop | `laptop` | 0.60 | 0.82 | 0.55 |
| monitor | `tv` | 0.50 | 0.50 | 0.68 |
| mouse | `mouse` | 0.40 | 0.77 | 0.74 |
| smartphone | `cell phone` | 0.40 | 0.67 | 0.25 |
| tablet, printer, speaker, all_in_one_pc | — | — | 0 | 0 |

Shu o‘lchovdan olingan qarorlar:

- `mouse` chegarasi 0.90 → **0.70**: 0.90 da model eshitilmaydi (R ~0.45),
  0.70 da P 0.83 / R 0.56; 0.40 da F1 eng yuqori, lekin soxta hodisa
  ko‘payadi — shu sababli loyihaning kichik obyekt chegarasi tanlandi.
- `desktop_pc` 0.90 → **0.60**: bu sinf modelda yo‘q, 0.90 chegara esa
  kelajakdagi o‘qitilgan modelni ham o‘tkazmasdi.
- `tv` → **Monitor**: 164 dalil kadrida 2 ta `tv` qutisi bor edi; ikkalasi
  ham monitor. Qayta nomlashdan keyin jami quti soni o‘zgarmadi (162 → 162),
  `person` va `laptop` da regressiya yo‘q.
- `keyboard` chegarasi **0.70 da qoldirildi**: 83 yorliqlangan kadrda faqat
  klaviatura bo‘yicha qayta o‘lchov (barcha kadrlar, soxta qutilar ham
  hisobga olingan): 0.55 da P 1.00 / R 0.72, 0.70 da P 1.00 / R 0.68, 0.50 da
  1 ta soxta quti. Farq jami ~2 quti, shuning uchun loyihaning “odam bo‘lmagan
  toifalar 0.60 dan past bo‘lmasin” qoidasi buzilmadi. Eslatma: haqiqiy dalil
  kadrlarida (164 ta) klaviatura 0.50 da ham chiqmaydi — chegara emas, kamera
  masofasi va model muammosi (2-bo‘lim).
- `monitor` va `laptop` chegaralari **o‘zgartirilmadi** (0.60): ular uchun past
  chegara aniqlikni tushirmasdan faqat soxta qutilarni ko‘paytiradi.
- `imgsz` 960 o‘lchandi: `laptop` R 0.55 → 0.64, `mouse` R 0.65 → 0.76 (bir xil
  P da), lekin CPU da kechikish 314 ms → 656 ms (3.2 fps → 1.5 fps). Shu sababli
  standart `640` qoldirildi; GPU bo‘lsa `.env` da `YOLO_IMAGE_SIZE=960` qo‘ying.

---

## 2. Kadrlarni yig‘ish

Model faqat o‘zi o‘rgangan ko‘rinishni yaxshi taniydi. Shuning uchun kadrlar
**aynan ishlatiladigan kameralardan** olinishi kerak.

1. Kameralarni o‘rnatib, burchak/yorug‘likni yakuniy holatga keltiring.
2. Panel orqali tez yig‘ish: **`/training`** sahifasida (faqat admin) toifa tanlanadi,
   rasmlar yuklanadi yoki **kameradan kadr** olinadi. Har bir toifa uchun 10–15 ta
   namuna `data/samples/<toifa>/` ga tushadi. Ular datasetga shu buyruq bilan nusxalanadi:

   ```bat
   .venv\Scripts\python.exe scripts\train_yolo.py --import-samples dataset
   ```

   Buyruq mavjud faylni ustiga yozmaydi (`<sinf>__<nom>.jpg`) va har sinf uchun
   nechta rasm qo‘shilganini chiqaradi. Keyin ularni **belgilash** kerak (3-bo‘lim) —
   belgilanmagan rasm o‘qitishda qatnashmaydi.
3. Ko‘proq kadr kerak bo‘lsa, kameradan yuklab oling:

   ```bat
   .venv\Scripts\python.exe scripts\test_camera.py --code CAM-01 --seconds 60 --save 20
   ```

   `--save` sonini oshirib (masalan 50) turli vaqtlarda bir necha marta yugurtiring.
   Kadrlar `data/debug/` ga tushadi.

   RTSP’dan to‘g‘ridan-to‘g‘ri yozish uchun `ffmpeg` ham ishlatilishi mumkin:

   ```bat
   ffmpeg -rtsp_transport tcp -i "rtsp://admin:parol@192.168.1.64:554/Streaming/Channels/101" ^
       -vf fps=1 dataset_raw\CAM-01\%%04d.jpg
   ```

4. Miqdor (tavsiya):

   * har bir jihoz sinfi uchun kamida **300–500** ta belgilangan obyekt;
   * `mouse`, `keyboard` kabi kichik obyektlar uchun **500+** (ular katta kadrda
     juda kichik);
   * kamida **50 ta** `val` kadr (yaxshisi 100+);
   * kun/kech, yorug‘/soya, ikki xil burchak, qisman to‘silgan holatlar;
   * **bo‘sh (background) kadrlar** ham qo‘shing — 10–15%: aks holda model har
     qanday dog‘ni jihoz deb topadi.

Maxfiylik: kadrlarda xodimlar yuzi bo‘lishi mumkin. Belgilashdan oldin yuzlarni
xiralashtirish yoki faqat jihozlarga qaratilgan kadrlarni tanlash tavsiya etiladi;
kadrlarni ruxsatsiz tarqatmang.

### 2.1. Ochiq datasetdan qo‘shimcha kadrlar (ixtiyoriy)

COCO modeli `monitor`, `printer`, `speaker`, `tablet` sinflarini **umuman
bilmaydi**, `mouse` esa qo‘lda tik tutilganda `cell phone` bo‘lib qoladi. Buni
faqat o‘qitish tuzatadi — va buning uchun bir necha yuz kadr kerak. O‘z
kamerangizdan yig‘ilguncha, ochiq to‘plamdan boshlanish nuqtasi sifatida
foydalanish mumkin:

`hhh134/YOLO-DeskDataset` — 1 646 kadr, YOLO `txt` label, 80 sinf (stol
buyumlari). Skript faqat **ma’nosi bir xil** sinflarni oladi:

| Dataset sinfi | Toifa | Izoh |
| --- | --- | --- |
| 8 `laptop` | `laptop` | |
| 9 `mouse` | `mouse` | asosiy foyda: qo‘lda, stolda, turli burchakda |
| 10 `keyboard` | `keyboard` | |
| 11 `monitor` | `monitor` | COCO da bu sinf yo‘q |
| 14 `mobile phone` | `smartphone` | |
| 15 `tablet` | `tablet` | COCO da bu sinf yo‘q |
| 71 `printer` | `printer` | to‘plamda atigi ~16 kadr |
| 76 `speaker` | `speaker` | to‘plamda ~58 kadr |

Ataylab **qoldirilgan** sinflar: `computer` (7) — tizim blokimi yoki monitor,
noaniq; `cable` (17) — LAN kabel emas, oddiy sim; `headphones`, `charger`,
`power bank`, `socket` — loyihada alohida toifa yo‘q.

**Tayanch o‘lchov** (76 ta sinov kadri, `yolov8s.pt`, `conf=0.25`, IoU 0.5):

| Sinf | P | R | Izoh |
| --- | --- | --- | --- |
| `mouse` | 0.68 | 0.80 | stolda yaxshi, qo‘lda tik tutilganda chalkashadi |
| `keyboard` | 0.58 | 0.81 | |
| `laptop` | 0.68 | 0.69 | |
| `smartphone` | 0.45 | **0.31** | aynan shu yerda “maus telefon bo‘lib qoldi” muammosi |
| `monitor` | — | **0.00** | 28 ta obyekt, bittasi ham topilmadi (COCO da sinf yo‘q) |
| `tablet` | — | **0.00** | 20 ta obyekt |
| `printer` | — | **0.00** | 6 ta obyekt |
| `speaker` | — | **0.00** | 5 ta obyekt |

Ya’ni COCO modeli bilan 4 ta toifa **umuman** ishlamaydi, `smartphone` esa
har uchinchi holatda topiladi. Bu raqamlarni o‘zingiz takrorlashingiz mumkin:
`data/models/equipment_desk_*.pt` modelini `yolov8s.pt` bilan bir xil kadrlarda
solishtirib ko‘ring (6-bo‘limdagi `val` buyrug‘i).

```bat
REM 1) nima tanlanishini ko‘rish (rasm yuklanmaydi, faqat label indeksi)
.venv\Scripts\python.exe scripts\import_public_dataset.py --dry-run

REM 2) har sinfdan 60 kadr -> dataset\images\train + labels\train
.venv\Scripts\python.exe scripts\import_public_dataset.py --max-per-class 60

REM 3) sinov to‘plami (datasetning o‘z val bo‘limidan)
.venv\Scripts\python.exe scripts\import_public_dataset.py --split val --max-per-class 12
```

Skript `data.yaml` ni, mavjud kadrlarni va `data/models` ni **o‘zgartirmaydi**:
faqat yangi fayl qo‘shadi, sinf raqamlarini loyiha tartibiga o‘giradi va
normallashtirilmagan/buzilgan label satrlarini tashlab yuboradi. Nomlar
barqaror (`mouse__hfdesk_1a2b3c4d`), shuning uchun qayta yugurtirish dublikat
yaratmaydi. Label indeksi `data/.cache/yolo_desk/` da keshlanadi — HuggingFace
ko‘p so‘rovga `429` qaytaradi, skript kutib qayta urinadi.

**Cheklovlar (bilib turish shart):**

1. To‘plamda `person` sinfi **yo‘q**. Faqat shu kadrlarda o‘qitilgan model
   xodimlarni ko‘rmaydi — yuzni tanish va inventar hodisalari ishlamaydi.
   Shuning uchun `train_yolo.py` datasetda `person` bo‘lmasa modelni
   **avtomatik almashtirmaydi** (faqat `--deploy` bilan majburlash mumkin).
   Kamera kadrlarini ham qo‘shing: `--import-samples`.
2. Kadrda bog‘lanmagan obyektlar (krujka, qalam, kitob) labeldan chiqariladi —
   model ularni fon deb o‘rganadi. Bu kichik shovqin; o‘z kamera kadrlaringiz
   bilan aralashtirilsa ta’siri kamayadi.
3. Ochiq to‘plam — **boshlanish**, yakun emas: burchak, yorug‘lik va masofa
   sizning kamerangiznikidan farq qiladi. Yakuniy sifatni o‘z kadrlaringiz beradi.

---

## 3. Belgilash (labeling)

### 3.1. Oldindan belgilash (pre-label)

Qo‘lda chizishni yengillashtirish uchun model o‘zi qutilar taklif qiladi:

```bat
.venv\Scripts\python.exe scripts\autolabel_dataset.py --dataset dataset
```

Skript nima qiladi:

* har bir rasmni joriy model bilan o‘tkazadi (`yolov8s.pt` yoki
  `data\models\equipment_best.pt`) va topilgan qutilarni YOLO `.txt` formatida yozadi;
* papka nomidagi sinfni ustuvor qiladi: `mouse__mouse_001.jpg` dagi “maus emas”
  degan quti `mouse` sinfiga o‘tkaziladi (`CONFUSIONS` jadvali bo‘yicha);
* shakl/maydon qoidasi (`plausible_box`) bo‘yicha shubhali qutilarni tashlaydi;
* `dataset\preview\<split>\` ga model (yashil) va tuzatilgan (sariq) qutilarni
  chizib qo‘yadi;
* hech qanday quti qolmagan rasm uchun bo‘sh `.txt` **yozmaydi** — `--check`
  ularni “tasdiqsiz” deb ko‘rsatadi (bo‘sh fayl “belgilangan” bo‘lib qolmasin).

Foydali bayroqlar: `--conf 0.25`, `--imgsz 960`, `--no-preview`, `--dry-run`,
`--overwrite` (eski `.txt` larni qayta yozadi), `--no-hint` (papka bo‘yicha
to‘g‘rilashni o‘chiradi).

Natijani `dataset\preview\` dagi rasmlarda **albatta ko‘rib chiqing** va xatolarni
LabelImg/CVAT bilan tuzating. Model faqat o‘zi biladigan sinflarni taklif qiladi:
COCO modelida `printer`, `ups`, `lan_cable` kabi sinflar umuman yo‘q, ularni
qo‘lda chizish shart. Avtomatik quti — **boshlanish nuqtasi, yakun emas**.

### 3.2. Qo‘lda tekshirish

Vositalar: [labelImg](https://github.com/HumanSignal/labelImg),
[label-studio](https://labelstud.io/), CVAT, Roboflow (YOLOv8 formatida eksport).

Qoidalar:

* Format: har rasm uchun `.txt`, har qator `class_id cx cy w h`
  (0..1 oralig‘ida normallashtirilgan, markaz koordinatasi).
* Box jihozga **tig‘iz** joylashsin, soyasi/orqasi emas.
* Bir obyektni ikki marta belgilamang; bir jismoniy jihoz uchun bitta box
  (`computer_case` + `desktop_pc` chalkash bo‘lsa — bittasini tanlang va izchil
  bo‘ling).
* Qisman ko‘ringan (`< 30%`) yoki juda loyqa obyektlarni belgilamaslik mumkin.
* `person` sinfini butun tanaga (boshdan oyoq) qo‘ying — faqat yuz emas.

Namuna:

```
0 0.512 0.634 0.181 0.402
8 0.702 0.418 0.094 0.128
10 0.238 0.771 0.052 0.090
```

---

## 4. Dataset tuzilmasi

```bat
.venv\Scripts\python.exe scripts\train_yolo.py --init dataset
```

Yaratiladi:

```
dataset/
  data.yaml
  README.md            # qisqa yo‘riqnoma
  images/train/*.jpg
  images/val/*.jpg
  labels/train/*.txt
  labels/val/*.txt
```

`data.yaml` (27 sinf, `CLASS_NAMES` bilan aynan bir xil tartibda) avtomatik yoziladi.

**Split qoidasi:** `train`/`val` ni tasodifiy emas, **vaqt va scena bo‘yicha**
ajrating. Bir videoning qo‘shni kadrlari (deyarli bir xil) `val` ga tushsa,
metrika yolg‘on yuqori chiqadi, real kamerada esa model yomon ishlaydi.

Tekshirish:

```bat
.venv\Scripts\python.exe scripts\train_yolo.py --check dataset\data.yaml
```

Skript sinflar soni, rasm/label juftligi va minimal kadr sonini tekshiradi.

`val` bo‘sh qolsa (barcha kadrlar `images/train` da), bir qismini ajrating:

```bat
.venv\Scripts\python.exe scripts\train_yolo.py --split-val 0.2
```

Har bir sinfdan kamida bittasi `val` ga tushadi (belgilangan kadrlar ustuvor,
chunki belgilanmagan kadr `val` metrikasiga hissa qo‘shmaydi) va har sinfda
kamida bitta kadr `train` da qoladi. Buyruq bir marta bajariladi: `images/val`
da rasm bo‘lsa qayta bo‘linmaydi (`--force` bilan majburlash mumkin).

---

## 5. O‘qitish

GPU (tavsiya):

```bat
.venv\Scripts\python.exe scripts\train_yolo.py --data dataset\data.yaml --run ^
    --epochs 80 --imgsz 640 --batch 16 --device 0
```

CPU (sekin, kichik dataset uchun):

```bat
.venv\Scripts\python.exe scripts\train_yolo.py --data dataset\data.yaml --run ^
    --epochs 40 --imgsz 640 --batch 4 --device cpu
```

`--run` bo‘lmasa skript faqat `yolo detect train ...` buyrug‘ini chop etadi.

Asosiy parametrlar:

| Parametr | Nima uchun |
| --- | --- |
| `--model` | Boshlang‘ich model (`yolov8s.pt`; kichik obyektlar uchun `yolov8m.pt`) |
| `--epochs` | 60–120 odatiy; `patience=25` erta to‘xtadi |
| `--imgsz` | 640 standart; kichik obyektlar (mouse, keyboard) uchun 960/1280 |
| `--batch` | GPU xotirasiga qarab 8–32; xotira yetmasa — kamaytiring |
| `--device` | `0` GPU, `cpu` |
| `--name` | `runs/detect/<name>`; versiyalash uchun `equipment_v2` |

Natijalar `runs/detect/equipment/` da: `weights/best.pt`, `weights/last.pt`,
`results.png`, `confusion_matrix.png`, `val_batch*.jpg`.

Muvaffaqiyatli `--run` yakunida `best.pt` avtomatik
`data/models/equipment_best.pt` ga nusxalanadi — lekin **faqat** `labels/train`
bo‘yicha belgilangan rasm soni `MIN_DEPLOY_LABELS = 100` dan ko‘p bo‘lsa.
Aks holda skript modelni almashtirmaydi va sababini yozadi: kichik datasetda
o‘qitilgan model boshqa 27 sinfni yomonlashtirishi mumkin. Ataylab majburlash
uchun `--deploy` bayrog‘i bor (masalan, faqat bitta sinfni sinab ko‘rishda).

---

## 6. Natijani baholash

Terminalda `mAP50`, `mAP50-95`, har sinf bo‘yicha `P`/`R` chiqadi. Qo‘shimcha:

```bat
yolo detect val model=runs\detect\equipment\weights\best.pt data=dataset\data.yaml imgsz=640
```

Nimaga qarash kerak:

* `mAP50-95` — umumiy sifat (0.5+ yaxshi boshlanish, sinflar bo‘yicha bo‘linishi
  muhimroq).
* `confusion_matrix.png` — `monitor` va `laptop`, `desktop_pc` va `computer_case`
  chalkashmayaptimi.
* `val_batch*.jpg` — box’lar joyida va tig‘izmi.
* **Biznes testi:** 10–15 daqiqalik real yozuvni tizimdan o‘tkazing va
  noto‘g‘ri `EQUIPMENT_BROUGHT_IN/TAKEN_OUT` hodisalari sonini sanang. Metrika
  emas, hodisa aniqligi muhim.

Agar sifat past bo‘lsa: kadrlarni ko‘paytirish → belgilash sifatini tekshirish →
`imgsz` oshirish → kattaroq model (`yolov8m.pt`) → faqat keyin ko‘proq epoch.

---

## 7. Modelni tizimga ulash

1. `best.pt` ni `data/models/equipment_best.pt` ga qo‘ying (skript buni o‘zi
   qiladi).
2. `.env` da:

   ```ini
   YOLO_MODEL_PATH=data/models/equipment_best.pt
   YOLO_CONFIDENCE=0.35
   YOLO_IMAGE_SIZE=640
   YOLO_DEVICE=cpu
   ```

   Yoki Sozlamalar sahifasida tahrirlang.
3. Sozlamalar → **“YOLO modelini qayta yuklash”**.
4. `Jihozlar` sahifasida har bir toifaning `yolo_labels` ro‘yxatini modeldagi
   sinf nomlariga moslang va `min_confidence` ni sinf bo‘yicha sozlang.
5. Kameralarni qayta ishga tushiring (kamerani “Yangilash” yoki server restart).
6. Dashboard’da model holati “yuklangan” bo‘lishi va ogohlantirish yo‘qolishi kerak.

`scripts/test_camera.py` modelni ishlatmaydi; modelni tekshirish uchun serverni
ko‘tarib `/live` sahifasidagi overlay’ni kuzating.

---

## 8. Versiyalash va qayta o‘qitish

* Model faylini versiyalang: `equipment_best_v3_2026-11-01.pt`; `.env` dagi yo‘lni
  yangilang. Eski modelni bir muddat saqlab turing (rollback uchun).
* Har o‘qitish uchun qisqa yozuv: dataset hajmi, sinflar, `mAP`, qaysi kameralar,
  qanday muammo tuzatilgan.
* Qachon qayta o‘qitish: kamera/obyektiv o‘zgardi, yangi jihoz turi qo‘shildi,
  noto‘g‘ri hodisalar ko‘paydi, yorug‘lik sharoiti o‘zgardi.
* Bir xil test kadrlar to‘plamida eski va yangi modelni solishtiring — “yaxshiroq”
  so‘z bilan emas, raqam bilan tasdiqlansin.

---

## 9. Tez-tez uchraydigan muammolar

| Muammo | Sabab | Yechim |
| --- | --- | --- |
| `CUDA out of memory` | `batch`/`imgsz` katta | `--batch 8`, `--imgsz 640`, `--model yolov8n.pt` |
| Metrika yuqori, real kadrda yomon | `val` train bilan bir sahnadan | split’ni vaqt/scena bo‘yicha qayta qiling |
| Model jihozni topmaydi | Yorug‘lik/burchak mos emas | Shu kameradan kadr qo‘shib qayta o‘qiting |
| Har narsani jihoz deb topadi | Background kadr yo‘q, threshold past | Bo‘sh kadrlar qo‘shing, `YOLO_CONFIDENCE` ni 0.4–0.5 ga ko‘taring |
| Kichik obyekt yo‘qoladi | Kadrda juda kichik | Yuqoriroq aniqlikdagi oqim, `imgsz` 960+, `yolov8m.pt` |
| `data.yaml topilmadi` | Yo‘l xato | `--init dataset` dan keyin `--data dataset\data.yaml` |
| `best.pt topilmadi` | O‘qitish to‘xtagan | `runs/detect/<name>/` loglarini tekshiring |
| Yangi model ishlamayapti | Kesh/qayta yuklanmagan | Sozlamalar → modelni qayta yuklash, kameralarni yangilash |
| `Prodakshn modeli almashtirilmadi` | `labels/train` da 100 tadan kam belgilangan rasm | Datasetni to‘ldiring yoki `--deploy` bilan majburlang |
| `autolabel` hamma rasmni “bo‘sh” dedi | O‘qitilmagan model jihozni tanimaydi yoki `conf` baland | `--conf 0.25`, `--imgsz 960`, keyin qo‘lda belgilash |

---

## 10. Yakuniy checklist

- [ ] Kadrlar aynan ishlab chiqarish kameralaridan olingan
- [ ] `/training` namunalari `--import-samples` bilan datasetga qo‘shilgan va belgilangan
- [ ] 27 sinf tartibi `scripts/train_yolo.py` bilan bir xil
- [ ] `autolabel_dataset.py` natijasi `dataset\preview\` da ko‘rib chiqildi
- [ ] Har sinf uchun 300+ obyekt, `val` uchun 50+ kadr
- [ ] Background kadrlar qo‘shilgan
- [ ] `train_yolo.py --check` xatosiz o‘tdi
- [ ] `val` metrikasi va `confusion_matrix` ko‘rib chiqildi
- [ ] Real yozuvda noto‘g‘ri hodisalar soni qabul qilinarli
- [ ] `data/models/equipment_best.pt` yangilandi va UI’dan qayta yuklandi
- [ ] `Jihozlar` toifalaridagi `yolo_labels` mos
- [ ] Kameralar qayta ishga tushirildi va `/live` da overlay to‘g‘ri
