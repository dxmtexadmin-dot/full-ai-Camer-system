"""UTC-first time helpers.

Timestamps are stored in UTC (``DateTime(timezone=True)``) and only converted
for display, using the configured workplace timezone (Asia/Tashkent by
default).
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import get_settings

_TASHKENT_FALLBACK = timezone.utc  # replaced below when tzdata is unavailable


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def workplace_timezone() -> timezone | ZoneInfo:
    name = get_settings().timezone
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # On Windows the IANA database comes from the "tzdata" package. When it
        # is missing we degrade to a fixed UTC+5 offset for Asia/Tashkent
        # instead of crashing the whole dashboard.
        if name in {"Asia/Tashkent", "Uzbekistan"}:
            from datetime import timedelta

            return timezone(timedelta(hours=5), name="Asia/Tashkent")
        return timezone.utc


def as_utc(value: datetime) -> datetime:
    """Attach UTC to naive values (SQLite drops tzinfo on read)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_local(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return as_utc(value).astimezone(workplace_timezone())


def format_local(value: Optional[datetime], fmt: str = "%d.%m.%Y %H:%M:%S") -> str:
    local = to_local(value)
    return local.strftime(fmt) if local else "—"


def format_local_short(value: Optional[datetime]) -> str:
    return format_local(value, "%d.%m.%Y %H:%M")


def local_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Return [start, end) of a local calendar day, expressed in UTC."""
    tz = workplace_timezone()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day, time.max.replace(microsecond=0), tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def local_today() -> date:
    return datetime.now(workplace_timezone()).date()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip())
    return as_utc(parsed)


def cooldown_bucket(moment: datetime, seconds: int) -> int:
    """Integer bucket used to deduplicate events inside a cooldown window."""
    seconds = max(1, int(seconds))
    return int(moment.timestamp()) // seconds
