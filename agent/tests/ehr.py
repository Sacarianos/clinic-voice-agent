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
VISIT_TYPE_SYSTEM = "https://clinic.example/fhir/CodeSystem/visit-type"


def clinic_time(days: int, at: str) -> datetime:
    """A wall-clock time at the clinic, `days` after today there."""
    today = datetime.now(CLINIC_TIMEZONE).date()
    return datetime.combine(today + timedelta(days=days), time.fromisoformat(at), CLINIC_TIMEZONE)


@dataclass(frozen=True)
class Provider:
    provider_id: str
    name: str
    schedule_id: str


@dataclass(frozen=True)
class CallbackRequest:
    id: str
    reason: str
    emergency: bool
    patient_id: str | None


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

    def create_patient(self, *, given: str, family: str, birth_date: str, phone: str | None = None) -> str:
        return self._create(
            {
                "resourceType": "Patient",
                "active": True,
                "name": [{"use": "official", "family": family, "given": [given]}],
                "birthDate": birth_date,
                **({"telecom": [{"system": "phone", "value": phone, "use": "mobile"}]} if phone else {}),
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

    def callback_requests_from(self, phone_number: str) -> list["CallbackRequest"]:
        """The Callback Requests staff would see for this phone number, newest first."""
        tasks = self._search("Task", {"_sort": "-_lastUpdated", "_count": "100"})
        found = []
        for task in tasks:
            inputs = {item["type"]["text"]: item for item in task.get("input", [])}
            if inputs.get("callback phone number", {}).get("valueString") == phone_number:
                reference = task.get("for", {}).get("reference")
                found.append(
                    CallbackRequest(
                        id=task["id"],
                        reason=task["description"],
                        emergency=inputs["emergency"]["valueBoolean"],
                        patient_id=reference.removeprefix("Patient/") if reference else None,
                    )
                )
        return found

    def delete_callback_requests_from(self, phone_number: str) -> None:
        for request in self.callback_requests_from(phone_number):
            self._fhir.delete(f"Task/{request.id}").raise_for_status()

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

    def take_slot(self, slot_id: str) -> None:
        """Marks the Slot busy, as if another Caller booked it a moment ago."""
        response = self._fhir.get(f"Slot/{slot_id}")
        response.raise_for_status()
        self._fhir.put(f"Slot/{slot_id}", json={**response.json(), "status": "busy"}).raise_for_status()

    def create_appointment(self, patient_id: str, provider: Provider, start: datetime, visit_type: str) -> str:
        """A booked Appointment in a busy Slot of its own, as if the Patient booked it on an earlier call."""
        slot_id = self.create_slot(provider, start, status="busy")
        response = self._fhir.post(
            "Appointment",
            json={
                "resourceType": "Appointment",
                "status": "booked",
                "appointmentType": {"coding": [{"system": VISIT_TYPE_SYSTEM, "code": visit_type}]},
                "slot": [{"reference": f"Slot/{slot_id}"}],
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=30)).isoformat(),
                "participant": [{"actor": {"reference": f"Patient/{patient_id}"}, "status": "accepted"}],
            },
        )
        response.raise_for_status()
        # Not on the created list: delete_created_records finds it through its Patient.
        return response.json()["id"]

    def appointment(self, appointment_id: str) -> dict:
        response = self._fhir.get(f"Appointment/{appointment_id}")
        response.raise_for_status()
        return response.json()

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
