"""Patient data never reaches logs or traces.

Whole calls run through the real conversation with DEBUG logging and tracing to the local Langfuse
stand-in. Afterwards every byte they produced is searched for the test Patients' names, dates of birth
and phone numbers.
"""

import io
import logging
import random
import sys
from dataclasses import dataclass
from datetime import date

import pytest
from ehr import clinic_time
from fakes import CallTool
from loguru import logger
from scripts import handoff, spoken, verify

from clinic_agent.logs import configure_logging

pytestmark = pytest.mark.scripted_only


@dataclass(frozen=True)
class PatientDetails:
    given: str
    family: str
    born: str  # YYYY-MM-DD
    phone: str  # E.164, which is also the number the Patient calls from

    @property
    def national_number(self) -> str:
        return self.phone.removeprefix("+1")

    @property
    def spoken_number(self) -> str:
        n = self.national_number
        return f"{n[:3]} {n[3:6]} {n[6:]}"

    def phi(self) -> list[str]:
        """Every form of this Patient's name, date of birth and phone number a log or trace might hold."""
        born = date.fromisoformat(self.born)
        n = self.national_number
        return [
            self.given,
            self.family,
            self.born,
            spoken(self.born),
            f"{born:%m/%d/%Y}",
            f"{born.month}/{born.day}/{born.year}",
            self.phone,
            self.phone.removeprefix("+"),
            n,
            self.spoken_number,
            f"{n[:3]}-{n[3:6]}-{n[6:]}",
            f"({n[:3]}) {n[3:6]}-{n[6:]}",
        ]


def found_phi(patient: PatientDetails, output: str) -> list[str]:
    folded = output.casefold()
    return [form for form in patient.phi() if form.casefold() in folded]


@pytest.fixture
def patient(ehr) -> PatientDetails:
    # A name no real word or Provider shares, so any hit is a leak.
    created = PatientDetails(
        given="Rosalind",
        family="Okonkwo",
        born=ehr.unused_birth_date(),
        phone=f"+1555{random.randrange(10**7):07d}",
    )
    ehr.create_patient(given=created.given, family=created.family, birth_date=created.born, phone=created.phone)
    return created


@pytest.fixture
def logs():
    """Everything logged, at DEBUG level, through the same logging setup the voice server uses."""
    captured = io.StringIO()
    root_handlers, root_level = logging.root.handlers[:], logging.root.level
    configure_logging("DEBUG", sink=captured)
    yield captured
    logger.remove()
    logger.add(sys.stderr)
    logging.root.handlers[:], logging.root.level = root_handlers, root_level


def exported_traces(langfuse, conversation_id: str) -> str:
    """Every byte sent to Langfuse so far, once this call's conversation has been exported.

    OTLP carries strings as raw UTF-8, so a name in any span shows up in the bytes as it is.
    """
    langfuse.wait_for_span("conversation", conversation_id)
    return "\n".join(body.decode("utf-8", errors="replace") for _, _, body in langfuse.exports)


async def test_a_verified_patient_booking_and_asking_for_a_callback_leaves_no_patient_data_in_logs_or_traces(
    ehr, start_call, patient, logs, langfuse
):
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    nine = clinic_time(1, "09:00")
    nine_slot = ehr.create_slot(faraday, nine)

    async with start_call(
        [
            "Sure. What is your date of birth?",
            verify(patient.given, patient.family, patient.born),
            f"Thanks, {patient.given}. Who would you like to see, and when?",
            CallTool("find_slots", {"provider": faraday.name}),
            "Dr. Faraday has 9 AM tomorrow. What is the visit for?",
            CallTool("choose_slot", {"slot_id": nine_slot, "visit_type": "annual_physical"}),
            CallTool("book_appointment"),
            handoff("asked_for_person"),
        ],
        caller_phone=patient.phone,
        tracing=True,
    ) as call:
        await call.converse(
            [
                f"Hi, this is {patient.given} {patient.family}. I'd like to book an appointment.",
                f"{spoken(patient.born)}. My number is {patient.spoken_number}.",
                f"{faraday.name}, tomorrow please.",
                f"Nine, for my annual physical. That's for {patient.given} {patient.family}, born {spoken(patient.born)}.",
                "Yes.",
                f"Could someone call me back at {patient.phone} about something else?",
            ]
        )

        assert call.tool_results("book_appointment") == [{"outcome": "succeeded"}]
        assert call.ended

    traces = exported_traces(langfuse, call.conversation_id)
    assert "Dr. Imogen Faraday" in traces  # the conversation is in the trace, with patient data masked
    assert found_phi(patient, traces) == []
    assert found_phi(patient, logs.getvalue()) == []
