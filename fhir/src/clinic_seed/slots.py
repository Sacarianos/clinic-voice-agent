from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from clinic_seed.clinic import (
    BOOKING_WINDOW_DAYS,
    CLINIC_TIMEZONE,
    CLOSING_HOUR,
    OPENING_HOUR,
    PROVIDERS,
    SLOT_MINUTES,
    SLOT_SYSTEM,
)


def today_at_clinic() -> date:
    return datetime.now(ZoneInfo(CLINIC_TIMEZONE)).date()


def weekdays_in_window(first_day: date, days: int = BOOKING_WINDOW_DAYS) -> list[date]:
    """Weekdays from first_day through first_day + days, inclusive."""
    return [d for d in (first_day + timedelta(days=n) for n in range(days + 1)) if d.weekday() < 5]


def slot_starts(day: date) -> list[datetime]:
    zone = ZoneInfo(CLINIC_TIMEZONE)
    first = datetime(day.year, day.month, day.day, OPENING_HOUR, tzinfo=zone)
    count = (CLOSING_HOUR - OPENING_HOUR) * 60 // SLOT_MINUTES
    return [first + timedelta(minutes=SLOT_MINUTES * i) for i in range(count)]


def slot(schedule_id: str, provider_key: str, start: datetime) -> tuple[dict, str]:
    """A free Slot and the "system|value" identifier that keeps seeding it idempotent."""
    value = f"{provider_key}-{start:%Y-%m-%dT%H:%M}"
    end = start + timedelta(minutes=SLOT_MINUTES)
    resource = {
        "resourceType": "Slot",
        "identifier": [{"system": SLOT_SYSTEM, "value": value}],
        "schedule": {"reference": f"Schedule/{schedule_id}"},
        "status": "free",
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
    }
    return resource, f"{SLOT_SYSTEM}|{value}"


def slot_resources(schedule_ids: dict[str, str], first_day: date) -> list[tuple[dict, str]]:
    return [
        slot(schedule_ids[provider.key], provider.key, start)
        for day in weekdays_in_window(first_day)
        for provider in PROVIDERS
        for start in slot_starts(day)
    ]
