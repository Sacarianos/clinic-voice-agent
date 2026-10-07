from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

CLINIC_TZ = ZoneInfo("America/New_York")
HALF_HOURS = [timedelta(hours=8, minutes=30 * i) for i in range(18)]  # 8:00 through 16:30


def booking_window_weekdays() -> list[date]:
    today = datetime.now(CLINIC_TZ).date()
    days = [today + timedelta(days=n) for n in range(15)]
    return [d for d in days if d.weekday() < 5]


def at_clinic_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(CLINIC_TZ)


def test_every_provider_has_free_half_hour_slots_from_8_to_5_on_every_weekday_in_the_booking_window(fhir):
    schedules = fhir.search("Schedule")
    assert len(schedules) == 3

    for schedule in schedules:
        slots = fhir.search(f"Slot?status=free&schedule=Schedule/{schedule['id']}")
        starts = sorted(at_clinic_time(s["start"]) for s in slots if at_clinic_time(s["start"]).date() in set(booking_window_weekdays()))
        expected = [
            datetime.combine(day, datetime.min.time(), CLINIC_TZ) + offset
            for day in booking_window_weekdays()
            for offset in HALF_HOURS
        ]
        assert starts == expected


def test_every_slot_lasts_thirty_minutes(fhir):
    slots = fhir.search("Slot?status=free")

    assert slots
    assert {at_clinic_time(s["end"]) - at_clinic_time(s["start"]) for s in slots} == {timedelta(minutes=30)}


def test_there_are_no_slots_on_weekends_or_outside_clinic_hours(fhir):
    slots = fhir.search("Slot")

    assert slots
    for slot in slots:
        start, end = at_clinic_time(slot["start"]), at_clinic_time(slot["end"])
        assert start.weekday() < 5
        assert start.time() >= datetime.min.time().replace(hour=8)
        assert end.time() <= datetime.min.time().replace(hour=17)
