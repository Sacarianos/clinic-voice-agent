"""Test-side access to the real HAPI behind the adapter: sets up Patients before a test.

Tests never stub HAPI. Each test creates the records it needs, so it runs the same on the seeded
local stack and on the empty HAPI in CI, and deletes them afterwards because the seed tests count
what the clinic holds.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta

import httpx


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

    def create_patient(self, *, given: str, family: str, birth_date: str) -> str:
        response = self._fhir.post(
            "Patient",
            json={
                "resourceType": "Patient",
                "active": True,
                "name": [{"use": "official", "family": family, "given": [given]}],
                "birthDate": birth_date,
            },
        )
        response.raise_for_status()
        patient_id = response.json()["id"]
        self._created.append(f"Patient/{patient_id}")
        return patient_id

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
        bundle = self._fhir.get("Task", params={"_sort": "-_lastUpdated", "_count": "100"})
        bundle.raise_for_status()
        found = []
        for entry in bundle.json().get("entry", []):
            task = entry["resource"]
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

    def delete_created_records(self) -> None:
        while self._created:
            self._fhir.delete(self._created.pop()).raise_for_status()
        self._fhir.close()
