"""Test-side access to the real HAPI behind the adapter: sets up Patients before a test.

Tests never stub HAPI. Each test creates the records it needs, so it runs the same on the seeded
local stack and on the empty HAPI in CI, and deletes them afterwards because the seed tests count
what the clinic holds.
"""

import random
from datetime import date, timedelta

import httpx


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
        """A date of birth no Patient has, from the 1800s to keep clear of the seeded adults."""
        for _ in range(20):
            day = date(1800, 1, 1) + timedelta(days=random.randrange(36_500))
            bundle = self._fhir.get("Patient", params={"birthdate": day.isoformat(), "_summary": "count"})
            bundle.raise_for_status()
            if bundle.json()["total"] == 0:
                return day.isoformat()
        raise RuntimeError("Could not find an unused date of birth")

    def delete_created_records(self) -> None:
        while self._created:
            self._fhir.delete(self._created.pop()).raise_for_status()
        self._fhir.close()
