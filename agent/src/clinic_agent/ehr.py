"""The agent's side of the EHR adapter's HTTP API. The agent knows nothing about FHIR."""

from dataclasses import dataclass
from typing import Literal

import httpx

DEFAULT_EHR_ADAPTER_URL = "http://localhost:3000"


@dataclass(frozen=True)
class Verification:
    status: Literal["verified", "ambiguous", "not_verified"]
    patient_id: str | None = None


class EhrAdapter:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def verify_patient(self, *, given_name: str, family_name: str, date_of_birth: str) -> Verification:
        body = {"givenName": given_name, "familyName": family_name, "dateOfBirth": date_of_birth}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=5) as client:
            response = await client.post("/patients/verify", json=body)
        response.raise_for_status()
        result = response.json()
        return Verification(status=result["status"], patient_id=result.get("patientId"))

    async def create_callback_request(
        self, *, phone_number: str, reason: str, emergency: bool, patient_id: str | None = None
    ) -> str:
        """Files a Callback Request for clinic staff and returns its id. Raises httpx.HTTPError when it can't."""
        body = {"phoneNumber": phone_number, "reason": reason, "emergency": emergency}
        if patient_id:
            body["patientId"] = patient_id
        async with httpx.AsyncClient(base_url=self.base_url, timeout=5) as client:
            response = await client.post("/callback-requests", json=body)
        response.raise_for_status()
        return response.json()["callbackRequestId"]
