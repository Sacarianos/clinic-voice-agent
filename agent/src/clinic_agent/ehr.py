"""The agent's side of the EHR adapter's HTTP API. The agent knows nothing about FHIR."""

import asyncio
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import httpx

from clinic_agent.audit import AuditLog
from clinic_agent.timeouts import ADAPTER_REQUEST_TIMEOUT_SECS

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

    rejected: a business rule stopped it and nothing was written. reason says which rule.
    failed: nothing was written, so a retry with the same idempotency key is safe.
    unknown: it may or may not have been written. Read the EHR again before saying anything.
    """

    outcome: Literal["succeeded", "rejected", "failed", "unknown"]
    reason: str | None = None


class EhrAdapter:
    """audit_log records every write sent through this adapter, as writes.py describes. By default it is the
    file AUDIT_LOG_PATH names.
    """

    def __init__(self, base_url: str, audit_log: AuditLog | None = None):
        self.base_url = base_url.rstrip("/")
        self.audit_log = audit_log or AuditLog.from_env(os.environ)

    async def _request(self, method: str, path: str, **options) -> httpx.Response:
        """Raises httpx.TimeoutException when no answer has arrived within ADAPTER_REQUEST_TIMEOUT_SECS in all."""
        try:
            async with asyncio.timeout(ADAPTER_REQUEST_TIMEOUT_SECS):
                async with httpx.AsyncClient(base_url=self.base_url, timeout=ADAPTER_REQUEST_TIMEOUT_SECS) as client:
                    return await client.request(method, path, **options)
        except TimeoutError as timeout:
            raise httpx.TimeoutException(f"{method} {path}: no answer within {ADAPTER_REQUEST_TIMEOUT_SECS} s") from timeout

    async def verify_patient(
        self, *, given_name: str, family_name: str, date_of_birth: str, family_name_spelled: bool = False
    ) -> Verification:
        """family_name_spelled says the Caller spelled the surname, so an exact surname may pick one of several
        sound-alike Patients. Only the attempt that follows a spelling request may set it.
        """
        body = {
            "givenName": given_name,
            "familyName": family_name,
            "dateOfBirth": date_of_birth,
            "familyNameSpelled": family_name_spelled,
        }
        response = await self._request("POST", "/patients/verify", json=body)
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
        response = await self._request("POST", "/callback-requests", json=body)
        response.raise_for_status()
        return response.json()["callbackRequestId"]

    async def providers(self) -> list[Provider]:
        response = await self._request("GET", "/providers")
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
        response = await self._request("GET", "/slots", params=params)
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
        response = await self._request("GET", f"/slots/{slot_id}")
        response.raise_for_status()
        return response.json()["slot"]["status"] == "free"

    async def appointments(self, patient_id: str) -> list[Appointment]:
        """The Patient's upcoming appointments, earliest first."""
        response = await self._request("GET", "/appointments", params={"patientId": patient_id})
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

    async def release_slot(self, *, slot_id: str, idempotency_key: str) -> WriteOutcome:
        """Settles a Book or Reschedule given up on after an unknown answer: frees its Slot if the write holds it,
        and makes sure it never lands later.

        succeeded: the write holds the Slot no longer and never will. rejected with write_landed: it landed in full.
        """
        return await self._write(f"/slots/{slot_id}/release", {"idempotencyKey": idempotency_key})

    async def release_from_cancel(self, *, patient_id: str, appointment_id: str, idempotency_key: str) -> WriteOutcome:
        """Settles a Cancel given up on after an unknown answer: makes sure it never lands later.

        succeeded: the Appointment stays booked. rejected with write_landed: the Cancel landed, and is now finished.
        """
        body = {"patientId": patient_id, "idempotencyKey": idempotency_key}
        return await self._write(f"/appointments/{appointment_id}/cancel/release", body)

    async def _write(self, path: str, body: dict) -> WriteOutcome:
        try:
            response = await self._request("POST", path, json=body)
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
