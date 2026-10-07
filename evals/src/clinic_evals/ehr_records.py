"""The eval run's own records in HAPI: seeded before the call, read after it, and removed so runs never affect each other.

Each run gets a Patient with a date of birth no one else has, its own Provider with its own Slots,
and its own phone number. Removing them also undoes anything the agent did outside them: a Slot of
another Provider that the run's Patient took is freed again.
"""

import random
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from clinic_evals.record import AppointmentState, CallbackRequest, EndState, Seeded, SlotState
from clinic_evals.scenario import Scenario, SlotSpec

CLINIC_TIMEZONE = ZoneInfo("America/New_York")
PROVIDER_SYSTEM = "https://clinic.example/fhir/identifier/provider"
VISIT_TYPE_SYSTEM = "https://clinic.example/fhir/CodeSystem/visit-type"
SLOT_LENGTH = timedelta(minutes=30)


class EhrRecords:
    """One run's records. Use `seed`, then `end_state` after the call, then always `clean_up`."""

    def __init__(self, fhir_base_url: str):
        self._fhir = httpx.Client(
            base_url=fhir_base_url,
            headers={"accept": "application/fhir+json", "content-type": "application/fhir+json"},
            timeout=30,
        )
        self._created: list[str] = []
        self._caller_phone = f"+1555{random.randrange(10**7):07d}"
        self._patient_id: str | None = None
        self._slot_ids: dict[str, str] = {}

    def seed(self, scenario: Scenario) -> Seeded:
        birth_date = self._unused_birth_date()
        patient_id = self._patient_id = self._create(
            {
                "resourceType": "Patient",
                "active": True,
                "name": [{"use": "official", "family": scenario.patient.family, "given": [scenario.patient.given]}],
                "birthDate": birth_date,
            }
        )
        starts = {label: _clinic_time(spec) for label, spec in scenario.slots.items()}
        taken = {spec.slot for spec in scenario.appointments.values()}
        if scenario.provider:
            schedule_id = self._create_provider(scenario.provider.given, scenario.provider.family)
            self._slot_ids = {
                label: self._create_slot(schedule_id, start, "busy" if label in taken else "free")
                for label, start in starts.items()
            }
        appointment_ids = {
            label: self._create_appointment(patient_id, self._slot_ids[spec.slot], starts[spec.slot], spec.visit_type)
            for label, spec in scenario.appointments.items()
        }
        return Seeded(
            patient_id=patient_id,
            birth_date=birth_date,
            caller_phone=self._caller_phone,
            slot_ids=self._slot_ids,
            slot_starts=starts,
            appointment_ids=appointment_ids,
        )

    def end_state(self) -> EndState:
        appointments = [
            AppointmentState(
                appointment_id=resource["id"],
                slot_id=_slot_id(resource),
                visit_type=resource["appointmentType"]["coding"][0]["code"],
                status=resource["status"],
            )
            for resource in self._appointments_of_patient()
        ]
        slot_ids = list(dict.fromkeys([*self._slot_ids.values(), *(a.slot_id for a in appointments)]))
        return EndState(
            appointments=appointments,
            slots=[self._slot_state(slot_id) for slot_id in slot_ids],
            callback_requests=[request for _, request in self._callback_requests()],
        )

    def clean_up(self) -> None:
        if self._patient_id:
            for task_id, _ in self._callback_requests():
                self._fhir.delete(f"Task/{task_id}").raise_for_status()
            for appointment in self._appointments_of_patient():
                self._fhir.delete(f"Appointment/{appointment['id']}").raise_for_status()
                slot_id = _slot_id(appointment)
                if f"Slot/{slot_id}" not in self._created:
                    self._free(slot_id)
        while self._created:
            self._fhir.delete(self._created.pop()).raise_for_status()
        self._fhir.close()

    def _create(self, resource: dict) -> str:
        response = self._fhir.post(resource["resourceType"], json=resource)
        response.raise_for_status()
        resource_id = response.json()["id"]
        self._created.append(f"{resource['resourceType']}/{resource_id}")
        return resource_id

    def _search(self, resource_type: str, params: dict) -> list[dict]:
        response = self._fhir.get(resource_type, params={**params, "_count": "200"})
        response.raise_for_status()
        return [entry["resource"] for entry in response.json().get("entry", [])]

    def _unused_birth_date(self) -> str:
        for _ in range(20):
            day = date(1935, 1, 1) + timedelta(days=random.randrange(70 * 365))
            bundle = self._fhir.get("Patient", params={"birthdate": day.isoformat(), "_summary": "count"})
            bundle.raise_for_status()
            if bundle.json()["total"] == 0:
                return day.isoformat()
        raise RuntimeError("Could not find an unused date of birth")

    def _create_provider(self, given: str, family: str) -> str:
        """Creates the run's own Provider and returns the id of their Schedule."""
        practitioner_id = self._create(
            {
                "resourceType": "Practitioner",
                "identifier": [{"system": PROVIDER_SYSTEM, "value": f"eval-{uuid.uuid4()}"}],
                "active": True,
                "name": [{"use": "official", "family": family, "given": [given], "prefix": ["Dr."]}],
            }
        )
        return self._create(
            {"resourceType": "Schedule", "active": True, "actor": [{"reference": f"Practitioner/{practitioner_id}"}]}
        )

    def _create_slot(self, schedule_id: str, start: datetime, status: str) -> str:
        return self._create(
            {
                "resourceType": "Slot",
                "schedule": {"reference": f"Schedule/{schedule_id}"},
                "status": status,
                "start": start.isoformat(),
                "end": (start + SLOT_LENGTH).isoformat(),
            }
        )

    def _create_appointment(self, patient_id: str, slot_id: str, start: datetime, visit_type: str) -> str:
        # Not on the created list: clean_up removes every Appointment of the Patient first.
        response = self._fhir.post(
            "Appointment",
            json={
                "resourceType": "Appointment",
                "status": "booked",
                "appointmentType": {"coding": [{"system": VISIT_TYPE_SYSTEM, "code": visit_type}]},
                "slot": [{"reference": f"Slot/{slot_id}"}],
                "start": start.isoformat(),
                "end": (start + SLOT_LENGTH).isoformat(),
                "participant": [{"actor": {"reference": f"Patient/{patient_id}"}, "status": "accepted"}],
            },
        )
        response.raise_for_status()
        return response.json()["id"]

    def _appointments_of_patient(self) -> list[dict]:
        return self._search("Appointment", {"patient": f"Patient/{self._patient_id}"})

    def _slot_state(self, slot_id: str) -> SlotState:
        response = self._fhir.get(f"Slot/{slot_id}")
        response.raise_for_status()
        holding = self._search("Appointment", {"slot": f"Slot/{slot_id}"})
        return SlotState(
            slot_id=slot_id,
            status=response.json()["status"],
            appointment_ids=[a["id"] for a in holding if a["status"] != "cancelled"],
        )

    def _free(self, slot_id: str) -> None:
        """Gives back a Slot the run's Patient took outside the run's own Provider, as the seed made it."""
        response = self._fhir.get(f"Slot/{slot_id}")
        response.raise_for_status()
        slot = {key: value for key, value in response.json().items() if key != "extension"}
        self._fhir.put(f"Slot/{slot_id}", json={**slot, "status": "free"}).raise_for_status()

    def _callback_requests(self) -> list[tuple[str, CallbackRequest]]:
        """(Task id, Callback Request) for each one filed from the run's phone number."""
        found = []
        for task in self._search("Task", {"_sort": "-_lastUpdated"}):
            inputs = {item["type"]["text"]: item for item in task.get("input", [])}
            if inputs.get("callback phone number", {}).get("valueString") == self._caller_phone:
                found.append((task["id"], CallbackRequest(task["description"], inputs["emergency"]["valueBoolean"])))
        return found


def _slot_id(appointment: dict) -> str:
    return appointment["slot"][0]["reference"].removeprefix("Slot/")


def _clinic_time(spec: SlotSpec) -> datetime:
    """The Slot's start: its clinic weekday after today, at its wall-clock time."""
    day = datetime.now(CLINIC_TIMEZONE).date()
    weekdays = 0
    while weekdays < spec.weekday:
        day += timedelta(days=1)
        weekdays += day.weekday() < 5
    return datetime.combine(day, spec.at, CLINIC_TIMEZONE)
