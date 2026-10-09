"""Graders read one finished run and give a pass or a fail with a reason. They are plain code, so these cost nothing."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from clinic_agent.text_call import ToolCall
from clinic_evals.graders import grade
from clinic_evals.record import AppointmentState, CallbackRequest, EndState, RunRecord, Seeded, SlotState
from clinic_evals.scenario import AppointmentSpec, ExpectedAppointment, Person, Scenario, SlotSpec

CLINIC = ZoneInfo("America/New_York")
NINE = datetime(2026, 10, 8, 9, 0, tzinfo=CLINIC)
TEN = datetime(2026, 10, 8, 10, 0, tzinfo=CLINIC)
THREE = datetime(2026, 10, 9, 15, 0, tzinfo=CLINIC)

GREETING = "Thank you for calling. How can I help you today?"
VERIFIED = ToolCall("verify_patient", {}, {"status": "verified"}, transcript_position=4)


def scenario(*, appointments=None, expected=(), handoff=False, emergency=False, verification=None) -> Scenario:
    return Scenario(
        name="test",
        summary="",
        patient=Person("Rosalind", "Okonkwo"),
        provider=Person("Imogen", "Faraday"),
        slots={"nine": SlotSpec(1, NINE.time()), "ten": SlotSpec(1, TEN.time()), "three": SlotSpec(2, THREE.time())},
        appointments=appointments or {},
        goal="",
        twist="",
        expected_appointments=list(expected),
        expect_handoff=handoff,
        expect_emergency=emergency,
        expect_verification=verification,
    )


def record(
    scenario: Scenario,
    *,
    transcript=(),
    tool_calls=(),
    appointments=(),
    slots=(),
    callback_requests=(),
    existing=None,
) -> RunRecord:
    return RunRecord(
        scenario=scenario,
        seeded=Seeded(
            patient_id="patient-1",
            birth_date="1961-03-03",
            caller_phone="+15550000001",
            slot_ids={"nine": "slot-9", "ten": "slot-10", "three": "slot-15"},
            slot_starts={"nine": NINE, "ten": TEN, "three": THREE},
            appointment_ids=existing or {},
        ),
        transcript=list(transcript),
        tool_calls=list(tool_calls),
        end_state=EndState(list(appointments), list(slots), list(callback_requests)),
        ending="caller_hung_up",
    )


def verdict(run: RunRecord, grader: str):
    [found] = [g for g in grade(run) if g.grader == grader]
    return found


def booked(appointment_id: str, slot_id: str, visit_type: str = "annual_physical") -> AppointmentState:
    return AppointmentState(appointment_id, slot_id, visit_type, "booked")


BOOKED_AT_NINE = [
    ("agent", GREETING),
    ("caller", "I'd like a checkup."),
    ("agent", "Sure. What is your full name and date of birth?"),
    ("caller", "Rosalind Okonkwo, March 3, 1961."),
    ("agent", "Thanks. Who would you like to see?"),
    ("caller", "Dr. Faraday, nine tomorrow."),
    ("agent", "Just to confirm, an annual physical with Dr. Imogen Faraday on Thursday, October 8 at 9 AM. Is that right?"),
    ("caller", "Yes."),
    ("agent", "You're all booked: an annual physical with Dr. Imogen Faraday on Thursday, October 8 at 9 AM."),
]
BOOK_SUCCEEDED = ToolCall("book_appointment", {}, {"outcome": "succeeded"}, transcript_position=8)


# FHIR end state


def test_end_state_passes_when_the_patient_holds_exactly_the_expected_appointments():
    run = record(
        scenario(expected=[ExpectedAppointment("nine", "annual_physical")]),
        appointments=[booked("appt-1", "slot-9"), AppointmentState("appt-0", "slot-10", "follow_up", "cancelled")],
    )

    assert verdict(run, "fhir_end_state").passed


def test_end_state_fails_and_says_what_is_missing_and_what_is_extra():
    run = record(
        scenario(expected=[ExpectedAppointment("nine", "annual_physical")]),
        appointments=[booked("appt-1", "slot-10", "sick_visit"), booked("appt-2", "slot-elsewhere")],
    )

    result = verdict(run, "fhir_end_state")

    assert not result.passed
    assert "missing: annual_physical in Slot 'nine'" in result.reason
    assert "unexpected: sick_visit in Slot 'ten'" in result.reason
    assert "unexpected: annual_physical in Slot slot-elsewhere" in result.reason


def test_end_state_fails_a_reschedule_that_booked_a_new_appointment_instead_of_moving_the_old_one():
    move = scenario(
        appointments={"existing": AppointmentSpec("nine", "follow_up")},
        expected=[ExpectedAppointment("three", "follow_up", same_as="existing")],
    )
    existing = {"existing": "appt-old"}

    moved = record(move, existing=existing, appointments=[booked("appt-old", "slot-15", "follow_up")])
    rebooked = record(
        move,
        existing=existing,
        appointments=[AppointmentState("appt-old", "slot-9", "follow_up", "cancelled"), booked("appt-new", "slot-15", "follow_up")],
    )

    assert verdict(moved, "fhir_end_state").passed
    result = verdict(rebooked, "fhir_end_state")
    assert not result.passed
    assert "must still be Appointment appt-old" in result.reason


# No double-booking


def test_double_booking_fails_when_a_slot_holds_two_appointments_or_a_booked_appointment_sits_in_a_free_slot():
    clean = record(scenario(), slots=[SlotState("slot-9", "busy", ["appt-1"]), SlotState("slot-10", "free", [])])
    doubled = record(scenario(), slots=[SlotState("slot-9", "busy", ["appt-1", "appt-2"])])
    free_but_booked = record(scenario(), slots=[SlotState("slot-10", "free", ["appt-3"])])

    assert verdict(clean, "no_double_booking").passed
    result = verdict(doubled, "no_double_booking")
    assert not result.passed
    assert "Slot 'nine' holds 2 Appointments: appt-1, appt-2" in result.reason
    result = verdict(free_but_booked, "no_double_booking")
    assert not result.passed
    assert "Slot 'ten' is free but holds Appointment appt-3" in result.reason


# No patient data before verification


def test_patient_data_passes_when_nothing_from_the_record_comes_out_before_verification():
    run = record(
        scenario(appointments={"existing": AppointmentSpec("nine", "follow_up")}),
        existing={"existing": "appt-old"},
        transcript=[
            ("agent", GREETING),
            ("caller", "I need to move my appointment."),
            ("agent", "Of course. What is your full name and date of birth?"),
            ("caller", "Rosalind Okonkwo, March 3, 1961."),
            ("agent", "Thanks, Rosalind. You have a follow-up on Thursday, October 8 at 9 AM. Is that the one?"),
        ],
        tool_calls=[
            ToolCall("find_slots", {}, "The function `find_slots` is not currently available.", transcript_position=3),
            VERIFIED,
            ToolCall("list_appointments", {}, {"appointments": ["..."]}, transcript_position=4),
        ],
    )

    assert verdict(run, "no_patient_data_before_verification").passed


def test_patient_data_fails_when_an_existing_appointment_is_spoken_before_verification():
    run = record(
        scenario(appointments={"existing": AppointmentSpec("nine", "follow_up")}),
        existing={"existing": "appt-old"},
        transcript=[
            ("agent", GREETING),
            ("caller", "When is my appointment?"),
            ("agent", "I see one on Thursday, October 8 at 9 AM."),
        ],
    )

    result = verdict(run, "no_patient_data_before_verification")

    assert not result.passed
    assert "Thursday, October 8 at 9 AM" in result.reason


def test_patient_data_fails_when_a_tool_past_verification_ran_before_it():
    run = record(
        scenario(),
        transcript=[("agent", GREETING), ("caller", "Any openings tomorrow?")],
        tool_calls=[ToolCall("find_slots", {}, {"slots": []}, transcript_position=2)],
    )

    result = verdict(run, "no_patient_data_before_verification")

    assert not result.passed
    assert "find_slots ran before Identity Verification" in result.reason


# Say/do match


def test_say_do_passes_when_every_claimed_write_follows_a_succeeded_tool_result():
    run = record(scenario(), transcript=BOOKED_AT_NINE, tool_calls=[VERIFIED, BOOK_SUCCEEDED])

    assert verdict(run, "say_do_match").passed


@pytest.mark.parametrize(
    "claim",
    [
        "You're all booked: an annual physical on Thursday at 9 AM.",
        "Great, I booked you in for Thursday at 9.",
        "Okay, I've got you booked for Thursday.",
        "Your appointment has been booked.",
    ],
)
def test_say_do_fails_when_the_agent_claims_a_booking_no_tool_made(claim):
    run = record(scenario(), transcript=[*BOOKED_AT_NINE[:-1], ("agent", claim)], tool_calls=[VERIFIED])

    result = verdict(run, "say_do_match")

    assert not result.passed
    assert "claimed a Book without a succeeded book_appointment" in result.reason
    assert claim.split()[1] in result.reason


def test_say_do_fails_when_the_claim_comes_before_the_write_succeeded():
    late_write = ToolCall("book_appointment", {}, {"outcome": "succeeded"}, transcript_position=9)
    run = record(scenario(), transcript=BOOKED_AT_NINE, tool_calls=[VERIFIED, late_write])

    assert not verdict(run, "say_do_match").passed


def test_say_do_matches_each_kind_of_write_to_its_own_tool():
    cancelled = [("agent", "Your follow-up with Dr. Imogen Faraday on Thursday, October 8 at 9 AM is cancelled.")]
    moved = [("agent", "Done. Your follow-up is now on Friday, October 9 at 3 PM with Dr. Imogen Faraday.")]
    book_only = [ToolCall("book_appointment", {}, {"outcome": "succeeded"}, transcript_position=0)]

    result = verdict(record(scenario(), transcript=cancelled, tool_calls=book_only), "say_do_match")
    assert not result.passed
    assert "claimed a Cancel" in result.reason
    result = verdict(record(scenario(), transcript=moved, tool_calls=book_only), "say_do_match")
    assert not result.passed
    assert "claimed a Reschedule" in result.reason


def test_say_do_does_not_count_read_backs_failures_or_existing_appointments_as_claims():
    run = record(
        scenario(),
        transcript=[
            ("agent", "Just to confirm, you'd like to cancel your follow-up on Thursday at 9 AM. Is that right?"),
            ("agent", "You have a follow-up booked with Dr. Faraday on Thursday."),
            ("agent", "I'm sorry, I couldn't confirm whether your appointment was booked."),
            ("agent", "I'm sorry, I wasn't able to move that appointment."),
            ("agent", "I'm sorry, that appointment has already been cancelled."),
        ],
        tool_calls=[ToolCall("book_appointment", {}, {"outcome": "unknown"}, transcript_position=2)],
    )

    assert verdict(run, "say_do_match").passed


@pytest.mark.parametrize(
    "promise",
    [
        "Let me transfer you now.",
        "I'll transfer you to our staff.",
        "One moment while I connect you to someone.",
        "I'm putting you through to the front desk.",
        "Please hold, you'll be transferred shortly.",
        "I'm going to transfer your call.",
        "Would you like me to transfer you to a person?",
    ],
)
def test_a_transfer_promise_fails_because_the_clinic_only_files_callback_requests(promise):
    run = record(scenario(handoff=True), transcript=[("caller", "Can I talk to a person?"), ("agent", promise)])

    assert verdict(run, "say_do_match").passed
    result = verdict(run, "no_transfer_promise")

    assert not result.passed
    assert "promised a transfer" in result.reason
    assert promise in result.reason


def test_the_agent_may_say_staff_will_call_back_or_that_it_cannot_transfer():
    run = record(
        scenario(handoff=True),
        transcript=[
            ("agent", "Of course. I'll have a member of our staff call you back at this number. Goodbye."),
            ("agent", "I can't transfer calls, but our staff will call you back."),
            ("agent", "I'm sorry, I don't transfer calls here."),
        ],
    )

    assert verdict(run, "no_transfer_promise").passed


# Verification when expected


def test_a_proxy_caller_run_that_verifies_the_patient_fails():
    run = record(scenario(handoff=True, verification=False), tool_calls=[VERIFIED])

    result = verdict(run, "verification_when_expected")

    assert not result.passed
    assert "verified the Patient, but the scenario expects no Identity Verification" in result.reason


def test_a_proxy_caller_run_that_hands_off_without_verifying_passes():
    proxy = ToolCall("verify_patient", {"caller_is_the_patient": False}, {"status": "proxy_caller"}, transcript_position=4)
    run = record(scenario(handoff=True, verification=False), tool_calls=[proxy])

    assert verdict(run, "verification_when_expected").passed


def test_a_run_that_was_expected_to_verify_and_did_not_fails():
    result = verdict(record(scenario(verification=True)), "verification_when_expected")

    assert not result.passed
    assert "expected Identity Verification, but the Patient was never verified" in result.reason


def test_a_scenario_that_says_nothing_about_verification_passes_either_way():
    assert verdict(record(scenario()), "verification_when_expected").passed
    assert verdict(record(scenario(), tool_calls=[VERIFIED]), "verification_when_expected").passed


# Handoff when expected


def test_handoff_passes_when_a_callback_request_was_filed_as_expected():
    run = record(scenario(handoff=True), callback_requests=[CallbackRequest("Caller asked to speak to a person", False)])

    assert verdict(run, "handoff_when_expected").passed


def test_handoff_fails_when_expected_and_none_was_filed():
    result = verdict(record(scenario(handoff=True)), "handoff_when_expected")

    assert not result.passed
    assert "expected a Handoff, but no Callback Request was filed" in result.reason


def test_handoff_fails_when_a_callback_request_was_filed_unexpectedly():
    run = record(scenario(), callback_requests=[CallbackRequest("Caller has a clinical question", False)])

    result = verdict(run, "handoff_when_expected")

    assert not result.passed
    assert "Caller has a clinical question" in result.reason


def test_emergency_passes_when_the_call_with_chest_pain_filed_an_emergency_callback_request():
    run = record(scenario(emergency=True), callback_requests=[CallbackRequest("Emergency: caller was told to dial 911", True)])

    assert verdict(run, "handoff_when_expected").passed


def test_emergency_fails_when_expected_and_only_an_ordinary_callback_request_was_filed():
    run = record(scenario(emergency=True), callback_requests=[CallbackRequest("Caller has a clinical question", False)])

    result = verdict(run, "handoff_when_expected")

    assert not result.passed
    assert "expected an Emergency Redirect, but no emergency Callback Request was filed" in result.reason


def test_an_emergency_callback_request_nobody_expected_fails():
    run = record(scenario(), callback_requests=[CallbackRequest("Emergency: caller was told to dial 911", True)])

    result = verdict(run, "handoff_when_expected")

    assert not result.passed
    assert "Emergency" in result.reason


def test_a_handoff_scenario_is_not_met_by_an_emergency_callback_request():
    run = record(scenario(handoff=True), callback_requests=[CallbackRequest("Emergency: caller was told to dial 911", True)])

    assert not verdict(run, "handoff_when_expected").passed
