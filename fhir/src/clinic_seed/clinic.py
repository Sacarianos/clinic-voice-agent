"""Facts about the fictional clinic that the seed and its tests share."""

from dataclasses import dataclass

CLINIC_TIMEZONE = "America/New_York"
OPENING_HOUR = 8
CLOSING_HOUR = 17
SLOT_MINUTES = 30
BOOKING_WINDOW_DAYS = 14

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
PROVIDERS = (
    Provider("whitfield", "Marcus", "Whitfield", "Dr.", "MD", "Doctor of Medicine"),
    Provider("szczepanski", "Wojciech", "Szczepanski", "Dr.", "MD", "Doctor of Medicine"),
    Provider("kowalczyk", "Siobhan", "Kowalczyk", None, "NP", "Nurse Practitioner"),
)
