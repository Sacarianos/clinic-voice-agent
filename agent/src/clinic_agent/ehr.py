"""The agent's side of the EHR adapter's HTTP API. The agent knows nothing about FHIR."""

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import httpx

from clinic_agent.audit import AuditLog

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
class Appointment:
    appointment_id: str
    slot_id: str
    provider_name: str
    start: datetime  # in clinic wall-clock time, with its UTC offset
    visit_type: VisitType


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
    """audit_log records every write sent through this adapter (see writes.py). By default it is the file
    AUDIT_LOG_PATH names.
    """

    def __init__(self, base_url: str, audit_log: AuditLog | None = None):
        self.base_url = base_url.rstrip("/")
        self.audit_log = audit_log or AuditLog.from_env(os.environ)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=5)

    async def verify_patient(self, *, given_name: str, family_name: str, date_of_birth: str) -> Verification:
        body = {"givenName": given_name, "familyName": family_name, "dateOfBirth": date_of_birth}
        async with self._client() as client:
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
        async with self._client() as client:
            response = await client.post("/callback-requests", json=body)
        response.raise_for_status()
        return response.json()["callbackRequestId"]

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

    async def slot_is_free(self, slot_id: str) -> bool:
        async with self._client() as client:
            response = await client.get(f"/slots/{slot_id}")
        response.raise_for_status()
        return response.json()["slot"]["status"] == "free"

    async def appointments(self, patient_id: str) -> list[Appointment]:
        """The Patient's upcoming appointments, earliest first."""
        async with self._client() as client:
            response = await client.get("/appointments", params={"patientId": patient_id})
        response.raise_for_status()
        return [
            Appointment(a["appointmentId"], a["slotId"], a["providerName"], datetime.fromisoformat(a["start"]), a["visitType"])
            for a in response.json()["appointments"]
        ]

    async def book(
        self, *, patient_id: str, slot_id: str, visit_type: VisitType, idempotency_key: str
    ) -> WriteOutcome:
        body = {"patientId": patient_id, "slotId": slot_id, "visitType": visit_type, "idempotencyKey": idempotency_key}
        return await self._write("/appointments", body)

    async def reschedule(
        self, *, patient_id: str, appointment_id: str, slot_id: str, idempotency_key: str
    ) -> WriteOutcome:
        body = {"patientId": patient_id, "slotId": slot_id, "idempotencyKey": idempotency_key}
        return await self._write(f"/appointments/{appointment_id}/reschedule", body)

    async def cancel(self, *, patient_id: str, appointment_id: str, idempotency_key: str) -> WriteOutcome:
        body = {"patientId": patient_id, "idempotencyKey": idempotency_key}
        return await self._write(f"/appointments/{appointment_id}/cancel", body)

    async def _write(self, path: str, body: dict) -> WriteOutcome:
        try:
            async with self._client() as client:
                response = await client.post(path, json=body)
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
