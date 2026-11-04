"""Google Sheets ulash ustaxonasi: kalit, jadval ID, sinov va varaqlar."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from app.api import sheets_setup
from app.services import settings_service
from app.services.google_sheets_service import (
    EVENT_HEADER,
    GoogleSheetsService,
    CredentialsError,
    discover_service_account_file,
    failure_message,
    get_sheets_service,
    normalise_credentials,
    parse_spreadsheet_id,
    spreadsheet_id_warning,
)


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found in page"
    return match.group(1)


def login(client, username: str = "admin", password: str = "Admin123!") -> None:
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "next": "/", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def service_key(client) -> str:
    return csrf_from(client.get("/sheets").text)


class FakeWorksheet:
    def __init__(self, title: str) -> None:
        self.title = title
        self.values: list[list[str]] = []

    def get_all_values(self) -> list[list[str]]:
        return [list(row) for row in self.values]

    def update(self, values, range_name: str = "A1") -> None:
        self.values = [list(values[0])]

    def append_rows(self, rows, value_input_option: str = "RAW") -> None:
        self.values.extend([list(row) for row in rows])

    def append_row(self, row, value_input_option: str = "RAW") -> None:
        self.append_rows([row])

    def batch_clear(self, ranges) -> None:
        self.values = self.values[:1]


class FakeBook:
    def __init__(self, titles=("Sheet1",)) -> None:
        self.title = "Test jadval"
        self._sheets = {title: FakeWorksheet(title) for title in titles}

    def worksheets(self):
        return list(self._sheets.values())

    def worksheet(self, title: str):
        from gspread.exceptions import WorksheetNotFound

        if title not in self._sheets:
            raise WorksheetNotFound(title)
        return self._sheets[title]

    def add_worksheet(self, title: str, rows: int = 1000, cols: int = 12):
        self._sheets[title] = FakeWorksheet(title)
        return self._sheets[title]


KEY_JSON = {
    "type": "service_account",
    "project_id": "face-date-test",
    "private_key_id": "abc123def456",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n",
    "client_email": "face-date-sheets@face-date-test.iam.gserviceaccount.com",
    "client_id": "1234567890",
}


def configured_service(settings, tmp_path, *, extra=None) -> GoogleSheetsService:
    values = {
        "secrets_dir": tmp_path,
        "google_sheets_enabled": True,
        "google_spreadsheet_id": "test-spreadsheet-id-1234567890",
        "google_service_account_file": tmp_path / "google_service_account.json",
    }
    values.update(extra or {})
    return GoogleSheetsService(replace(settings, **values))


# -- pure helpers ---------------------------------------------------------
def test_parse_spreadsheet_id_accepts_url_and_bare_id():
    spreadsheet_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    assert parse_spreadsheet_id(f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid=0") == spreadsheet_id
    assert parse_spreadsheet_id(f"https://docs.google.com/open?id={spreadsheet_id}") == spreadsheet_id
    assert parse_spreadsheet_id(f"  {spreadsheet_id}  ") == spreadsheet_id


def test_parse_spreadsheet_id_rejects_garbage():
    with pytest.raises(ValueError):
        parse_spreadsheet_id("")
    with pytest.raises(ValueError):
        parse_spreadsheet_id("https://example.com/not-a-sheet")


def test_parse_spreadsheet_id_accepts_a_shared_edit_link():
    link = "https://docs.google.com/spreadsheets/d/112tZBWrVVifB3WAoEfMAWt1GWPWvXP-N8RtWoxPCpoE/edit?gid=0#gid=0"
    assert parse_spreadsheet_id(link) == "112tZBWrVVifB3WAoEfMAWt1GWPWvXP-N8RtWoxPCpoE"


def test_normalise_credentials_requires_service_account():
    with pytest.raises(CredentialsError):
        normalise_credentials({"type": "authorized_user", "client_email": KEY_JSON["client_email"]})
    with pytest.raises(CredentialsError):
        normalise_credentials("not json at all")
    with pytest.raises(CredentialsError):
        normalise_credentials({**KEY_JSON, "private_key": ""})
    parsed = normalise_credentials(json.dumps(KEY_JSON))
    assert parsed["client_email"] == KEY_JSON["client_email"]


# -- service --------------------------------------------------------------
def test_save_credentials_writes_a_private_file(settings, tmp_path):
    service = configured_service(settings, tmp_path)
    path, info = service.save_credentials(json.dumps(KEY_JSON))

    assert path == tmp_path / "google_service_account.json"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["client_email"] == KEY_JSON["client_email"]
    assert info["client_email"] == KEY_JSON["client_email"]

    reader = configured_service(settings, tmp_path, extra={"google_service_account_file": path})
    assert reader.credentials_info()["client_email"] == KEY_JSON["client_email"]
    assert reader.credentials_info()["key_id"] == "abc123def456"[:12]


def test_credentials_info_reports_missing_file(settings, tmp_path):
    service = configured_service(settings, tmp_path, extra={"google_service_account_file": tmp_path / "nope.json"})
    info = service.credentials_info()
    assert info["exists"] is False
    assert "topilmadi" in info["error"]


def test_forget_credentials_deletes_the_key(settings, tmp_path):
    service = configured_service(settings, tmp_path)
    path, _ = service.save_credentials(KEY_JSON)
    service.settings = replace(service.settings, google_service_account_file=path)

    assert service.forget_credentials() is True
    assert not path.exists()
    assert service.credentials_info()["exists"] is False


def test_verify_reports_disabled_state(settings):
    service = GoogleSheetsService(replace(settings, google_sheets_enabled=False))
    result = service.verify()
    assert result["ok"] is False
    assert "o‘chirilgan" in result["message"]


def test_verify_lists_missing_worksheets(settings, tmp_path):
    service = configured_service(settings, tmp_path)
    service.open_spreadsheet = lambda: FakeBook(["Sheet1"])  # type: ignore[method-assign]

    result = service.verify()
    assert result["ok"] is True
    assert result["spreadsheet_title"] == "Test jadval"
    assert set(result["missing"]) == {"Employees", "Equipment", "MovementEvents", "InventorySummary"}


def test_verify_explains_permission_errors(settings, tmp_path):
    service = configured_service(settings, tmp_path)

    def boom():
        raise RuntimeError("APIError: [403]: The caller does not have permission")

    service.open_spreadsheet = boom  # type: ignore[assignment]
    result = service.verify()
    assert result["ok"] is False
    assert "bo‘lishilmagan" in result["hint"]


def test_prepare_worksheets_creates_then_reuses(settings, tmp_path):
    pytest.importorskip("gspread")
    service = configured_service(settings, tmp_path)
    book = FakeBook(["Sheet1"])
    service.open_spreadsheet = lambda: book  # type: ignore[method-assign]

    first = service.prepare_worksheets()
    assert len(first["created"]) == 4
    assert book.worksheets()[1].values[0] == EVENT_HEADER or len(book.worksheets()) == 5

    second = service.prepare_worksheets()
    assert second["created"] == []


# -- HTTP page and endpoints ----------------------------------------------
def test_sheets_page_requires_admin(client, other_user_factory):
    other_user_factory("operator-two", role="operator")
    login(client, "operator-two", "Operator123!")
    response = client.get("/sheets")
    assert response.status_code == 403


def test_sheets_page_shows_three_steps(client):
    login(client)
    response = client.get("/sheets")
    assert response.status_code == 200
    for text in ("1-qadam", "2-qadam", "3-qadam", "Sinovdan o‘tkazish", "Varaqlarni tayyorlash"):
        assert text in response.text


def test_upload_credentials_saves_key_and_updates_env(client, settings, tmp_path, monkeypatch):
    service = configured_service(settings, tmp_path)
    captured: dict = {}

    def fake_update(values):
        captured.update(values)
        return list(values)

    monkeypatch.setattr(sheets_setup, "get_sheets_service", lambda: service)
    monkeypatch.setattr(settings_service, "update_env", fake_update)
    monkeypatch.setattr(settings_service, "refresh_runtime", lambda monitor: None)

    login(client)
    token = service_key(client)
    response = client.post(
        "/api/sheets/credentials",
        data={"csrf_token": token, "json_text": ""},
        files={"file": ("key.json", json.dumps(KEY_JSON).encode("utf-8"), "application/json")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/sheets"
    assert (tmp_path / "google_service_account.json").exists()
    assert "GOOGLE_SERVICE_ACCOUNT_FILE" in captured

    page = client.get("/sheets")
    assert KEY_JSON["client_email"] in page.text


def test_upload_rejects_a_non_service_account_json(client, settings, tmp_path, monkeypatch):
    service = configured_service(settings, tmp_path)
    monkeypatch.setattr(sheets_setup, "get_sheets_service", lambda: service)
    monkeypatch.setattr(settings_service, "update_env", lambda values: [])

    login(client)
    token = service_key(client)
    response = client.post(
        "/api/sheets/credentials",
        data={"csrf_token": token, "json_text": '{"type": "user"}'},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert not (tmp_path / "google_service_account.json").exists()

    page = client.get("/sheets")
    assert "Kalit qabul qilinmadi" in page.text


def test_spreadsheet_endpoint_parses_the_url(client, settings, tmp_path, monkeypatch):
    service = configured_service(settings, tmp_path)
    captured: dict = {}

    def fake_update(values):
        captured.update(values)
        return list(values)

    monkeypatch.setattr(sheets_setup, "get_sheets_service", lambda: service)
    monkeypatch.setattr(settings_service, "update_env", fake_update)
    monkeypatch.setattr(settings_service, "refresh_runtime", lambda monitor: None)

    login(client)
    token = service_key(client)
    spreadsheet_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    response = client.post(
        "/api/sheets/spreadsheet",
        data={
            "csrf_token": token,
            "spreadsheet": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            "interval": "300",
            "enable": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert captured["GOOGLE_SPREADSHEET_ID"] == spreadsheet_id
    assert captured["GOOGLE_SHEETS_SYNC_INTERVAL_SECONDS"] == "300"
    assert captured["GOOGLE_SHEETS_ENABLED"] == "true"


def test_spreadsheet_endpoint_rejects_bad_input(client, settings, tmp_path, monkeypatch):
    service = configured_service(settings, tmp_path)
    calls: list = []
    monkeypatch.setattr(sheets_setup, "get_sheets_service", lambda: service)
    monkeypatch.setattr(settings_service, "update_env", lambda values: calls.append(values) or [])

    login(client)
    token = service_key(client)
    response = client.post(
        "/api/sheets/spreadsheet",
        data={"csrf_token": token, "spreadsheet": "salom"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == []
    assert "Havola yoki ID noto‘g‘ri" in client.get("/sheets").text


def test_toggle_refuses_to_enable_without_configuration(client, settings, tmp_path, monkeypatch):
    service = GoogleSheetsService(
        replace(settings, secrets_dir=tmp_path, google_sheets_enabled=False, google_spreadsheet_id="")
    )
    calls: list = []
    monkeypatch.setattr(sheets_setup, "get_sheets_service", lambda: service)
    monkeypatch.setattr(settings_service, "update_env", lambda values: calls.append(values) or [])

    login(client)
    token = service_key(client)
    response = client.post(
        "/api/sheets/toggle",
        data={"csrf_token": token, "enabled": "on"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == []
    assert "Avval kalit va jadval ID’sini kiriting" in client.get("/sheets").text


def test_singleton_service_is_reused(settings):  # pragma: no cover - defensive
    assert get_sheets_service() is get_sheets_service()


def test_relative_helper_keeps_absolute_paths_outside_project(tmp_path):
    outside = Path(tmp_path) / "secrets" / "key.json"
    assert sheets_setup._relative(outside) == str(outside)


# -- guidance for broken configurations -----------------------------------
def test_spreadsheet_id_warning_flags_short_or_numeric_ids():
    assert spreadsheet_id_warning("") == ""
    assert spreadsheet_id_warning("1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789") == ""
    # 21 digits: a Drive/account id or a truncated copy, never a Sheets id.
    assert "44 belgidan" in spreadsheet_id_warning("114683324345281214563")
    assert "44 belgidan" in spreadsheet_id_warning("abc123")


def test_failure_message_explains_a_missing_key_file():
    missing = Path("C:/keys/gone.json")
    message, hint = failure_message(FileNotFoundError(2, "No such file or directory", str(missing)), missing)
    assert "topilmadi" in message
    assert "gone.json" in message
    assert "2-qadamda" in hint


def test_failure_message_keeps_the_google_error_text():
    message, hint = failure_message(RuntimeError("APIError: [403]: The caller does not have permission"), None)
    assert message.startswith("Ulanib bo‘lmadi")
    assert "bo‘lishilmagan" in hint


def test_verify_names_the_missing_key_file(settings, tmp_path):
    missing = tmp_path / "eskirgan_kalit.json"
    service = configured_service(settings, tmp_path, extra={"google_service_account_file": missing})
    service.open_spreadsheet = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
        FileNotFoundError(2, "No such file or directory", str(missing))
    )

    result = service.verify()
    assert result["ok"] is False
    assert "eskirgan_kalit.json" in result["message"]
    assert "2-qadamda" in result["hint"]


def test_sheets_page_warns_about_a_suspicious_spreadsheet_id(client, settings, tmp_path, monkeypatch):
    service = configured_service(
        settings, tmp_path, extra={"google_spreadsheet_id": "114683324345281214563"}
    )
    monkeypatch.setattr(sheets_setup, "get_sheets_service", lambda: service)

    login(client)
    page = client.get("/sheets")
    assert "Jadval ID shubhali" in page.text


# -- key discovery ---------------------------------------------------------
def test_discover_service_account_file_skips_junk(tmp_path):
    (tmp_path / "notes.json").write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    key = tmp_path / "face-date-1a2b3c.json"  # Google's random download name
    key.write_text(json.dumps(KEY_JSON), encoding="utf-8")

    assert discover_service_account_file(tmp_path) == key
    assert discover_service_account_file(tmp_path / "missing") is None


def test_credentials_path_falls_back_to_the_secrets_folder(settings, tmp_path):
    key = tmp_path / "loyiha-9f8e7d.json"
    key.write_text(json.dumps(KEY_JSON), encoding="utf-8")
    service = configured_service(
        settings, tmp_path, extra={"google_service_account_file": tmp_path / "gone.json"}
    )

    assert service.credentials_path == key
    info = service.credentials_info()
    assert info["exists"] is True
    assert info["client_email"] == KEY_JSON["client_email"]
    assert info["path"] == str(key)
    assert info["configured_path"] == str(tmp_path / "gone.json")


def test_configured_path_wins_over_a_discovered_key(settings, tmp_path):
    saved = tmp_path / "google_service_account.json"
    saved.write_text(json.dumps(KEY_JSON), encoding="utf-8")
    (tmp_path / "stale.json").write_text(json.dumps(KEY_JSON), encoding="utf-8")
    service = configured_service(settings, tmp_path, extra={"google_service_account_file": saved})

    assert service.credentials_path == saved
