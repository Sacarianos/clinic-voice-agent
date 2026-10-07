"""Test-side access to the real HAPI behind the adapter: sets up records before a test and reads them after.

Tests never stub HAPI. Each test creates the records it needs, so it runs the same on the seeded
local stack and on the empty HAPI in CI, and deletes them afterwards because the seed tests count
what the clinic holds.
"""

import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

CLINIC_TIMEZONE = ZoneInfo("America/New_York")
PROVIDER_SYSTEM = "https://clinic.example/fhir/identifier/provider"


def clinic_time(days: int, at: str) -> datetime:
    """A wall-clock time at the clinic, `days` after today there."""
    today = datetime.now(CLINIC_TIMEZONE).date()
    return datetime.combine(today + timedelta(days=days), time.fromisoformat(at), CLINIC_TIMEZONE)


@dataclass(frozen=True)
class Provider:
    provider_id: str
    name: str
    schedule_id: str


class Ehr:
    def __init__(self, fhir_base_url: str):
        self._fhir = httpx.Client(
            base_url=fhir_base_url,
            headers={"accept": "application/fhir+json", "content-type": "application/fhir+json"},
            timeout=30,
        )
        self._created: list[str] = []

    def _create(self, resource: dict) -> str:
        response = self._fhir.post(resource["resourceType"], json=resource)
        response.raise_for_status()
        resource_id = response.json()["id"]
        self._created.append(f"{resource['resourceType']}/{resource_id}")
        return resource_id

    def _search(self, query: str, params: dict) -> list[dict]:
        response = self._fhir.get(query, params=params)
        response.raise_for_status()
        return [entry["resource"] for entry in response.json().get("entry", [])]

    def create_patient(self, *, given: str, family: str, birth_date: str) -> str:
        return self._create(
            {
                "resourceType": "Patient",
                "active": True,
                "name": [{"use": "official", "family": family, "given": [given]}],
                "birthDate": birth_date,
            }
        )

    def unused_birth_date(self) -> str:
        """A plausible adult date of birth that no Patient has, so a test's own Patients are the only match."""
        for _ in range(20):
            day = date(1935, 1, 1) + timedelta(days=random.randrange(70 * 365))
            bundle = self._fhir.get("Patient", params={"birthdate": day.isoformat(), "_summary": "count"})
            bundle.raise_for_status()
            if bundle.json()["total"] == 0:
                return day.isoformat()
        raise RuntimeError("Could not find an unused date of birth")

    def create_provider(self, *, given: str, family: str) -> Provider:
        """A Provider of the test's own, so the Slots it gets belong to the test alone."""
        provider_id = f"test-{uuid.uuid4()}"
        practitioner_id = self._create(
            {
                "resourceType": "Practitioner",
                "identifier": [{"system": PROVIDER_SYSTEM, "value": provider_id}],
                "active": True,
                "name": [{"use": "official", "family": family, "given": [given], "prefix": ["Dr."]}],
            }
        )
        schedule_id = self._create(
            {"resourceType": "Schedule", "active": True, "actor": [{"reference": f"Practitioner/{practitioner_id}"}]}
        )
        return Provider(provider_id, f"Dr. {given} {family}", schedule_id)

    def create_slot(self, provider: Provider, start: datetime, status: str = "free") -> str:
        return self._create(
            {
                "resourceType": "Slot",
                "schedule": {"reference": f"Schedule/{provider.schedule_id}"},
                "status": status,
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=30)).isoformat(),
            }
        )

    def slot_status(self, slot_id: str) -> str:
        response = self._fhir.get(f"Slot/{slot_id}")
        response.raise_for_status()
        return response.json()["status"]

    def appointments_in_slot(self, slot_id: str) -> list[dict]:
        return self._search("Appointment", {"slot": f"Slot/{slot_id}"})

    def appointments_of(self, patient_id: str) -> list[dict]:
        return self._search("Appointment", {"patient": f"Patient/{patient_id}"})

    def delete_created_records(self) -> None:
        # Appointments the agent booked aren't on the list, and they point at the test's Slots and Patients.
        for record in self._created:
            if record.startswith("Patient/"):
                for appointment in self._search("Appointment", {"patient": record}):
                    self._fhir.delete(f"Appointment/{appointment['id']}").raise_for_status()
        while self._created:
            self._fhir.delete(self._created.pop()).raise_for_status()
        self._fhir.close()
