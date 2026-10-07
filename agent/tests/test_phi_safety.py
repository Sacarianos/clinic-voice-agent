"""Patient data never reaches logs or traces.

Whole calls run through the real conversation with DEBUG logging and tracing to the local Langfuse
stand-in. Afterwards every byte they produced is searched for the test Patients' names, dates of birth
and phone numbers.
"""

import io
import logging
import random
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date

import pytest
from ehr import clinic_time
from fakes import CallTool, RecordingTTS, ScriptedCaller, ScriptedLLM
from faulty_adapter import CANCEL, RESCHEDULE
from loguru import logger
from scripts import handoff, spoken, verify
from starlette.testclient import TestClient
from twilio_stream import hang_up, start_media_stream

from clinic_agent.ehr import EhrAdapter
from clinic_agent.logs import configure_logging
from clinic_agent.server import TwilioAccount, create_app
from clinic_agent.services import VoiceServices

pytestmark = pytest.mark.scripted_only


@dataclass(frozen=True)
class PatientDetails:
    given: str
    family: str
    born: str  # YYYY-MM-DD
    phone: str  # E.164, which is also the number the Patient calls from
    patient_id: str

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
    given, family, born = "Rosalind", "Okonkwo", ehr.unused_birth_date()
    phone = f"+1555{random.randrange(10**7):07d}"
    patient_id = ehr.create_patient(given=given, family=family, birth_date=born, phone=phone)
    return PatientDetails(given, family, born, phone, patient_id)


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


async def test_rescheduling_and_cancelling_through_injected_faults_leaves_no_patient_data_in_logs_or_traces(
    ehr, start_call, faulty_adapter, patient, logs, langfuse
):
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    nine, ten, two_pm = clinic_time(1, "09:00"), clinic_time(1, "10:00"), clinic_time(2, "14:00")
    to_move = ehr.create_appointment(patient.patient_id, faraday, nine, "annual_physical")
    to_cancel = ehr.create_appointment(patient.patient_id, faraday, ten, "sick_visit")
    new_slot = ehr.create_slot(faraday, two_pm)
    faulty_adapter.inject("half_write", into=RESCHEDULE)
    faulty_adapter.inject("server_error", into=CANCEL, times=2)

    async with start_call(
        [
            "Of course. What is your full name and date of birth?",
            verify(patient.given, patient.family, patient.born),
            CallTool("list_appointments"),
            "You have a 9 AM and a 10 AM with Dr. Faraday tomorrow. Which one?",
            CallTool("choose_appointment_to_reschedule", {"appointment_id": to_move}),
            CallTool("find_slots", {"provider": faraday.name, "from_date": two_pm.date().isoformat()}),
            "Dr. Faraday has 2 PM that day. Would that work?",
            CallTool("choose_slot", {"slot_id": new_slot}),
            CallTool("reschedule_appointment"),
            CallTool("list_appointments"),
            CallTool("choose_appointment_to_cancel", {"appointment_id": to_cancel}),
            CallTool("cancel_appointment"),
        ],
        adapter_url=faulty_adapter.url,
        caller_phone=patient.phone,
        tracing=True,
    ) as call:
        await call.converse(
            [
                "Hi, I need to change my appointments.",
                f"{patient.given} {patient.family}, {spoken(patient.born)}.",
                f"Move the 9 o'clock to the afternoon two days from now. You can reach me on {patient.spoken_number}.",
                "2 PM is perfect.",
                "Yes.",
                f"Now cancel the 10 o'clock for {patient.given} please.",
                "Yes.",
            ]
        )

        assert call.tool_results("reschedule_appointment") == [{"outcome": "succeeded"}]
        assert call.tool_results("cancel_appointment") == [{"outcome": "failed"}]
        assert len(faulty_adapter.injected) == 3
        [filed] = ehr.callback_requests_from(patient.phone)
        assert call.ended

    traces = exported_traces(langfuse, call.conversation_id)
    assert "Dr. Imogen Faraday" in traces
    assert found_phi(patient, traces) == []
    assert found_phi(patient, logs.getvalue()) == []


def test_a_phone_call_that_verifies_and_books_leaves_no_patient_data_in_logs_or_traces(
    ehr, ehr_adapter_url, patient, logs, langfuse
):
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    nine = clinic_time(1, "09:00")
    nine_slot = ehr.create_slot(faraday, nine)
    call_sid = f"CA{uuid.uuid4().hex}"
    caller = ScriptedCaller(
        [
            f"Hi, it's {patient.given} {patient.family}, I'd like to book an appointment.",
            f"{spoken(patient.born)}.",
            f"{faraday.name}, tomorrow please.",
            "Nine o'clock, for my annual physical.",
            "Yes, that's right.",
        ]
    )
    tts = RecordingTTS()
    app = create_app(
        lambda: VoiceServices(
            stt=caller,
            llm=ScriptedLLM(
                [
                    "I can help with that, what is your date of birth?",
                    verify(patient.given, patient.family, patient.born),
                    f"Thanks {patient.given}, who would you like to see, and when?",
                    CallTool("find_slots", {"provider": faraday.name}),
                    "Dr. Faraday has 9 AM tomorrow, what is the visit for?",
                    CallTool("choose_slot", {"slot_id": nine_slot, "visit_type": "annual_physical"}),
                    CallTool("book_appointment"),
                ]
            ),
            tts=tts,
            ehr=EhrAdapter(ehr_adapter_url),
        ),
        twilio=TwilioAccount(account_sid="AC00000000000000000000000000000001", auth_token="test-auth-token"),
        hang_up_through_twilio=False,
        tracing=True,
    )

    try:
        with TestClient(app) as client, client.websocket_connect("/ws") as twilio:
            start_media_stream(twilio, from_number=patient.phone, call_sid=call_sid)
            _wait_for(lambda: "booked" in " ".join(tts.spoken), "the agent to say the appointment is booked")
            hang_up(twilio, app)
    finally:
        ehr.delete_callback_requests_from(patient.phone)

    traces = exported_traces(langfuse, call_sid)
    assert "Dr. Imogen Faraday" in traces
    assert found_phi(patient, traces) == []
    assert found_phi(patient, logs.getvalue()) == []


def _wait_for(condition, what: str, seconds: float = 10):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"gave up waiting for {what}")
        time.sleep(0.05)
