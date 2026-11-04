"""Google Sheets mirror: retry policy, idempotent upsert, failure bookkeeping."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import SyncLog
from app.models.enums import EventType, SyncStatus
from app.services import event_service
from app.services.google_sheets_service import (
    EMPLOYEE_HEADER,
    EVENT_HEADER,
    GoogleSheetsService,
    SheetsUnavailable,
)


class FakeWorksheet:
    def __init__(self, title: str, values: list[list[str]] | None = None) -> None:
        self.title = title
        self.values: list[list[str]] = values or []
        self.appended: list[list[str]] = []
        self.updates: list[tuple[str, list[list[str]]]] = []

    def get_all_values(self) -> list[list[str]]:
        return [list(row) for row in self.values]

    def update(self, values, range_name: str = "A1") -> None:
        self.updates.append((range_name, values))
        if range_name == "A1":
            self.values = [list(values[0])]
        elif range_name.startswith("A2"):
            self.values = [self.values[0]] + [list(row) for row in values]
        else:
            index = int(range_name[1:]) - 1
            while len(self.values) <= index:
                self.values.append([])
            self.values[index] = list(values[0])

    def append_rows(self, rows, value_input_option: str = "RAW") -> None:
        self.appended.extend([list(row) for row in rows])
        self.values.extend([list(row) for row in rows])

    def append_row(self, row, value_input_option: str = "RAW") -> None:
        self.append_rows([row], value_input_option)

    def batch_clear(self, ranges) -> None:  # pragma: no cover - used by replace_body
        for item in ranges:
            self.values = self.values[:1] if self.values else []


class FakeBook:
    def __init__(self) -> None:
        self.sheets: dict[str, FakeWorksheet] = {}

    def worksheet(self, title: str) -> FakeWorksheet:
        # gspread raises WorksheetNotFound; the service then creates the sheet.
        # Returning an existing/blank worksheet exercises the same code path.
        return self.sheets.setdefault(title, FakeWorksheet(title))

    def add_worksheet(self, title: str, rows: int = 1000, cols: int = 12) -> FakeWorksheet:
        self.sheets[title] = FakeWorksheet(title)
        return self.sheets[title]


def enabled_service(settings, book: FakeBook) -> GoogleSheetsService:
    """A service that believes it is configured, but talks to an in-memory book."""
    enabled = replace(
        settings,
        google_sheets_enabled=True,
        google_spreadsheet_id="test-spreadsheet",
        google_service_account_file=Path("secrets/test-service-account.json"),
    )
    service = GoogleSheetsService(enabled)
    service.open_spreadsheet = lambda: book  # type: ignore[method-assign]
    return service


def test_retry_repeats_until_success(settings):
    service = GoogleSheetsService(settings)
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("temporary")
        return "ok"

    assert service._retry(flaky, attempts=3, base_delay=0) == "ok"
    assert calls["count"] == 3


def test_retry_raises_after_last_attempt(settings):
    service = GoogleSheetsService(settings)

    def always_fails():
        raise RuntimeError("boom")

    with pytest.raises(SheetsUnavailable):
        service._retry(always_fails, attempts=2, base_delay=0)


def test_sync_is_skipped_when_disabled(session, settings):
    service = GoogleSheetsService(settings)
    result = service.sync(session, triggered_by="test")
    session.commit()

    assert result.ok is False
    assert "o‘chirilgan" in result.message
    log = session.scalars(select(SyncLog)).one()
    assert log.status == SyncStatus.FAILED.value


def test_employee_rows_are_upserted_not_duplicated(session, settings):
    from app.services import employee_service

    employee_service.create_employee(session, first_name="Ali", last_name="Valiyev", department="IT")
    session.commit()

    book = FakeBook()
    service = enabled_service(settings, book)
    first = service.sync(session, triggered_by="test")
    assert first.ok is True
    session.commit()

    worksheet = book.sheets["Employees"]
    assert worksheet.values[0] == EMPLOYEE_HEADER
    assert len(worksheet.values) == 2

    second = service.sync(session, triggered_by="test")
    assert second.ok is True
    session.commit()
    # No duplicate rows: the same Employee ID is matched and nothing appended.
    assert len(book.sheets["Employees"].values) == 2


def test_event_rows_are_deduplicated_by_event_id(session, settings):
    book = FakeBook()
    service = enabled_service(settings, book)

    event, _ = event_service.create_event(
        session, event_type=EventType.PERSON_ENTERED, camera_code="CAM-01", person_track_id=1
    )
    session.commit()

    first = service.sync(session, triggered_by="test")
    session.commit()
    assert first.ok is True
    assert first.sheets["MovementEvents"] == 1

    worksheet = book.sheets["MovementEvents"]
    assert worksheet.values[0] == EVENT_HEADER
    assert worksheet.values[1][0] == event.public_id

    # A second run must not append the same Event ID again.
    service.sync(session, triggered_by="test")
    session.commit()
    assert [row[0] for row in worksheet.values[1:]] == [event.public_id]


def test_already_present_event_is_marked_synced(session, settings):
    book = FakeBook()
    service = enabled_service(settings, book)
    event, _ = event_service.create_event(
        session, event_type=EventType.PERSON_ENTERED, camera_code="CAM-01", person_track_id=1
    )
    session.commit()

    # Pretend a previous run already wrote the row but crashed before the commit.
    worksheet = service._worksheet(book, "MovementEvents", EVENT_HEADER)
    worksheet.values.append([event.public_id, "", "", "", "", "", "", "", "", "", ""])

    service.sync(session, triggered_by="test")
    session.commit()
    assert event.sheets_sync_status == SyncStatus.SYNCED.value
    assert [row[0] for row in worksheet.values[1:]] == [event.public_id]


def test_failed_upload_marks_event_failed(session, settings):
    book = FakeBook()
    service = enabled_service(settings, book)
    event, _ = event_service.create_event(
        session, event_type=EventType.PERSON_ENTERED, camera_code="CAM-01", person_track_id=1
    )
    session.commit()

    class BrokenWorksheet(FakeWorksheet):
        def append_row(self, row, value_input_option: str = "RAW") -> None:
            raise RuntimeError("quota exceeded")

    # One attempt only (the real retry sleeps between attempts).
    def immediate(operation, **kwargs):
        try:
            return operation()
        except RuntimeError as exc:
            raise SheetsUnavailable(str(exc)) from exc

    service._worksheet = lambda *args, **kwargs: BrokenWorksheet(title="MovementEvents", values=[EVENT_HEADER])  # type: ignore[assignment]
    service._retry = immediate  # type: ignore[assignment]

    result = service.sync(session, triggered_by="test")
    session.commit()

    assert result.ok is True
    assert result.failed == 1
    assert event.sheets_sync_status == SyncStatus.FAILED.value


def test_status_reports_configuration(session, settings):
    service = GoogleSheetsService(settings)
    status = service.status()
    assert status["enabled"] is False
    assert status["configured"] is False
    assert status["interval_seconds"] >= 15


def test_sync_reports_a_missing_key_instead_of_logging_a_traceback(session, settings, tmp_path):
    configured = replace(
        settings,
        google_sheets_enabled=True,
        google_spreadsheet_id="test-spreadsheet",
        google_service_account_file=str(tmp_path / "missing.json"),
        secrets_dir=str(tmp_path / "secrets"),
    )
    result = GoogleSheetsService(configured).sync(session, triggered_by="test")

    assert result.ok is False
    assert "kalit" in result.message.lower()
    assert "FileNotFound" not in result.message
