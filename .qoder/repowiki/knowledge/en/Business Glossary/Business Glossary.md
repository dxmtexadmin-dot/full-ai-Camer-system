---
kind: business_term
name: Business Glossary
category: business_term
scope:
    - '**'
---

### Sozlamalar
- Definition：The Settings page (`/settings`, admin-only) where operators edit whitelisted `.env` values atomically, reload the YOLO model, refresh the face gallery, manage users, and view audit/SyncLog entries.
- Aliases：Settings、sozlamalar sahifasi

### Tekshirish kerak
- Definition：A manual-review flag on movement events indicating an uncertain association between person and equipment; stored as `extra.requires_review=true` and surfaced on the `/review` page for operator confirmation or rejection.
- Aliases：requires_review、tekshirish

### Noma'lum shaxs
- Definition：An unidentified-person event emitted when face recognition confidence is below threshold; such events are created as `PENDING` candidates awaiting operator linkage to a registered employee.
- Aliases：unknown person、noma'lum

### Kutilmoqda
- Definition：Pending inventory direction: `kirim kutilmoqda` (pending_in) and `chiqim kutilmoqda` (pending_out) count CONFIRMED-but-unapproved ledger candidates; they contribute to projected balance but not expected balance until confirmed.
- Aliases：pending、kutilmoqda holat

### Prognoz
- Definition：Projected inventory balance computed as `expected + pending_in − pending_out`; reflects what the balance would be if all pending candidates were approved.
- Aliases：projected balance

### Farq
- Definition：Discrepancy indicator: when physical asset counts are entered, it equals `expected − registered assets`; a non-zero value signals a mismatch requiring investigation.
- Aliases：discrepancy

### SIMULYATSIYA
- Definition：Simulation mode for cameras (`SIMULATION_MODE=true` or camera type `simulation`); injects synthetic frames with room/person/laptop objects so the full pipeline can be exercised without a real camera feed.
- Aliases：simulyatsiya、simulation

### Chiziq kesishish
- Definition：Line-crossing logic: a virtual line (two points in `PROCESSING_WIDTH×PROCESSING_HEIGHT` coordinates) determines entry vs exit direction; combined with hysteresis, cooldown, and optional direction inversion to produce `PERSON_ENTERED/EXITED` events.
- Aliases：line crossing、virtual line

### Bog'lanish ballari
- Definition：Association scoring formula combining ownership motion (0.50), box proximity (0.25), temporal match (0.15), and direction match (0.10) to rate person-equipment links as STRONG/WEAK/AMBIGUOUS/NONE.
- Aliases：association score、bog'lanish

### Dedupe kaliti
- Definition：Event deduplication key composed of a prefix (`mv:` / `pp:` / `sys:`) plus track/camera/category, preventing duplicate writes within the cooldown window (`EVENT_COOLDOWN_SECONDS`).
- Aliases：dedupe key、event id

### Dalil rasmi
- Definition：Evidence JPEG snapshot saved under `data/evidence/YYYY-MM-DD/` for each person/equipment event; accessible only via the protected `/api/events/{id}/evidence` endpoint for operator+ roles.
- Aliases：evidence、dalil

### SyncLog
- Definition：Audit log of Google Sheets synchronization attempts, visible on the Settings page, showing last sync time, sent row count, and error count.
- Aliases：sheets sync log
