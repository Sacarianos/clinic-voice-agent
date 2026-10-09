"""Facts about the fictional clinic that the seed and its tests share.

The clinic's hours, time zone, Booking Window and Providers come from clinic.json at the repo root, which
the adapter and the agent read too. In Docker the file is mounted at /clinic.json.
"""

import json
from dataclasses import dataclass
from pathlib import Path

_CLINIC = json.loads((Path(__file__).resolve().parents[3] / "clinic.json").read_text(encoding="utf-8"))

CLINIC_TIMEZONE: str = _CLINIC["timezone"]
OPENING_HOUR: int = _CLINIC["openingHour"]
CLOSING_HOUR: int = _CLINIC["closingHour"]
SLOT_MINUTES: int = _CLINIC["slotMinutes"]
BOOKING_WINDOW_DAYS: int = _CLINIC["bookingWindowDays"]

SYSTEM_BASE = "https://clinic.example/fhir/identifier"
MRN_SYSTEM = f"{SYSTEM_BASE}/mrn"
PROVIDER_SYSTEM = f"{SYSTEM_BASE}/provider"
SCHEDULE_SYSTEM = f"{SYSTEM_BASE}/schedule"
SLOT_SYSTEM = f"{SYSTEM_BASE}/slot"


@dataclass(frozen=True)
class Provider:
    key: str
    given: str
    family: str
    prefix: str | None
    role_code: str
    role_display: str


# Szczepanski and Kowalczyk are the surnames speech-to-text is most likely to mangle.
PROVIDERS = tuple(
    Provider(p["key"], p["given"], p["family"], p["prefix"], p["roleCode"], p["roleDisplay"]) for p in _CLINIC["providers"]
)
