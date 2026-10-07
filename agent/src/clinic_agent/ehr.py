"""The agent's side of the EHR adapter's HTTP API. The agent knows nothing about FHIR."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import httpx

DEFAULT_EHR_ADAPTER_URL = "http://localhost:3000"

PartOfDay = Literal["morning", "afternoon"]
VisitType = Literal["annual_physical", "sick_visit", "follow_up"]


@dataclass(frozen=True)
class Verification:
    status: Literal["verified", "ambiguous", "not_verified"]
    patient_id: str | None = None


@dataclass(frozen=True)
class Provider:
    provider_id: str
    name: str


@dataclass(frozen=True)
class Slot:
    slot_id: str
    provider_id: str
    provider_name: str
    start: datetime  # in clinic wall-clock time, with its UTC offset


@dataclass(frozen=True)
class SlotSearch:
    slots: list[Slot]
    booking_window_last_day: date


@dataclass(frozen=True)
class WriteOutcome:
    """What a write did. Only "succeeded" lets the agent tell the Caller it is done.

    rejected: a business rule stopped it (reason says which) and nothing was written.
    failed: nothing was written, so a retry with the same idempotency key is safe.
    unknown: it may or may not have been written. Read the EHR again before saying anything.
    """

    outcome: Literal["succeeded", "rejected", "failed", "unknown"]
    reason: str | None = None


class EhrAdapter:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=5)

    async def verify_patient(self, *, given_name: str, family_name: str, date_of_birth: str) -> Verification:
        body = {"givenName": given_name, "familyName": family_name, "dateOfBirth": date_of_birth}
        async with self._client() as client:
            response = await client.post("/patients/verify", json=body)
        response.raise_for_status()
        result = response.json()
        return Verification(status=result["status"], patient_id=result.get("patientId"))

    async def providers(self) -> list[Provider]:
        async with self._client() as client:
            response = await client.get("/providers")
        response.raise_for_status()
        return [Provider(p["providerId"], p["providerName"]) for p in response.json()["providers"]]

    async def find_slots(
        self,
        *,
        provider_id: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        part_of_day: PartOfDay | None = None,
        limit: int = 3,
    ) -> SlotSearch:
        query = {"providerId": provider_id, "from": from_date, "to": to_date, "partOfDay": part_of_day}
        params = {name: value for name, value in query.items() if value} | {"limit": limit}
        async with self._client() as client:
            response = await client.get("/slots", params=params)
        response.raise_for_status()
        result = response.json()
        return SlotSearch(
            slots=[
                Slot(s["slotId"], s["providerId"], s["providerName"], datetime.fromisoformat(s["start"]))
                for s in result["slots"]
            ],
            booking_window_last_day=date.fromisoformat(result["bookingWindowLastDay"]),
        )

    async def book(
        self, *, patient_id: str, slot_id: str, visit_type: VisitType, idempotency_key: str
    ) -> WriteOutcome:
        body = {"patientId": patient_id, "slotId": slot_id, "visitType": visit_type, "idempotencyKey": idempotency_key}
        try:
            async with self._client() as client:
                response = await client.post("/appointments", json=body)
        except httpx.ConnectError:
            return WriteOutcome("failed")
        except httpx.TransportError:
            # The request may have reached the adapter, and the adapter the EHR.
            return WriteOutcome("unknown")
        if response.is_server_error:
            return WriteOutcome("unknown")
        response.raise_for_status()
        result = response.json()
        return WriteOutcome(result["outcome"], result.get("reason"))
