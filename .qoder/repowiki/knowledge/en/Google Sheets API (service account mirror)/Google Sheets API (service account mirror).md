---
kind: external_dependency
name: Google Sheets API (service account mirror)
slug: google-sheets-api
category: external_dependency
category_hints:
    - auth_protocol
    - client_constraint
scope:
    - '**'
source_files:
    - requirements.txt
    - app/services/google_sheets_service.py
    - scripts/sync_google_sheets.py
---

### Google Sheets API via gspread + google-auth
- Role: Read-only mirror of the local SQLite database into four worksheets (`Employees`, `Equipment`, `MovementEvents`, `InventorySummary`); never the source of truth.
- Auth: Google Cloud service account JSON key placed at `secrets/service_account.json`; spreadsheet shared with the service account email as Editor.
- Client constraint: Writes use exponential retry (up to `GOOGLE_SHEETS_MAX_RETRIES`); failed rows stay in `FAILED` state and are re-queued on next sync; `MovementEvents` deduplicated by Event ID; background scheduler runs every `GOOGLE_SHEETS_SYNC_INTERVAL_SECONDS`.
- Verify exact gspread methods and worksheet names against current `app/services/google_sheets_service.py`.