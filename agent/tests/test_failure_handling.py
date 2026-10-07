"""EHR failures during a write, injected by the adapter, driven through the text transport.

The agent retries a failed write once with the same idempotency key, re-reads the EHR after an
unknown one before saying anything, and hands off with a Callback Request when it still can't
confirm the write. It never says a write is done unless the EHR shows it done.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from ehr import clinic_time
from fakes import CallTool
from faulty_adapter import BOOK, CANCEL, LIST_APPOINTMENTS, RESCHEDULE
from scripts import answer_read_back, spoken, verify

from clinic_agent.holding import HOLDING_LINE

pytestmark = pytest.mark.scripted_only

# The adapter's stalled_write fault applies a write this long after the adapter gave up on it.
STALLED_WRITE_LANDS_AFTER_SECS = 3


@asynccontextmanager
async def at_booking_read_back(ehr, start_call, adapter_url):
    """A call by a Verified Patient, stopped at the Read-back of a Book. Yields (call, patient_id, slot_id)."""
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    slot_id = ehr.create_slot(faraday, clinic_time(1, "09:00"))
    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("find_slots", {"provider": faraday.name}),
            "Dr. Faraday has 9 AM tomorrow. What is the visit for?",
            CallTool("choose_slot", {"slot_id": slot_id, "visit_type": "sick_visit"}),
            answer_read_back("yes"),
            CallTool("book_appointment"),
        ],
        adapter_url=adapter_url,
    ) as call:
        await call.converse(["I'd like an appointment.", f"Rosalind Okonkwo, {spoken(born)}.", "9 AM, a sick visit."])
        assert call.state == "read_back"
        yield call, patient_id, slot_id


async def test_a_failed_book_is_retried_once_with_the_same_key_and_booked_once(ehr, start_call, faulty_adapter):
    faulty_adapter.inject("server_error", into=BOOK)
    async with at_booking_read_back(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id):
        await call.say("Yes.")

        assert faulty_adapter.injected == ["server_error POST /appointments"]
        assert call.tool_results("book_appointment") == [{"outcome": "succeeded"}]
        [appointment] = ehr.appointments_of(patient_id)
        assert appointment["slot"] == [{"reference": f"Slot/{slot_id}"}]
        assert ehr.slot_status(slot_id) == "busy"
        assert "booked" in call.agent_lines[-1]
        assert ehr.callback_requests_from(call.caller_phone) == []


async def test_a_book_that_fails_twice_hands_off_with_a_callback_request_and_claims_nothing(ehr, start_call, faulty_adapter):
    faulty_adapter.inject("server_error", into=BOOK, times=2)
    async with at_booking_read_back(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id):
        reply = await call.say("Yes.")

        assert len(faulty_adapter.injected) == 2
        assert call.tool_results("book_appointment") == [{"outcome": "failed"}]
        assert ehr.appointments_of(patient_id) == []
        assert ehr.slot_status(slot_id) == "free"
        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert filed.patient_id == patient_id
        assert "could not book" in filed.reason.lower()
        assert "wasn't able to book" in reply
        assert "call you back" in reply
        assert "booked" not in reply
        assert call.ended


async def test_a_book_that_timed_out_but_landed_is_found_by_re_reading_and_not_sent_again(ehr, start_call, faulty_adapter):
    faulty_adapter.inject("timeout", into=BOOK)
    async with at_booking_read_back(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id):
        await call.say("Yes.")

        assert faulty_adapter.sent(BOOK) == 1
        assert faulty_adapter.sent(LIST_APPOINTMENTS) == 1
        assert call.tool_results("book_appointment") == [{"outcome": "succeeded"}]
        [appointment] = ehr.appointments_of(patient_id)
        assert appointment["slot"] == [{"reference": f"Slot/{slot_id}"}]
        assert "booked" in call.agent_lines[-1]


async def test_a_slow_book_has_a_holding_line_before_the_answer(ehr, start_call, faulty_adapter):
    faulty_adapter.inject("timeout", into=BOOK)
    async with at_booking_read_back(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id):
        reply = await call.say("Yes.")

        assert reply.startswith(HOLDING_LINE)
        assert "booked" in call.agent_lines[-1]
        assert call.agent_lines[-1] != HOLDING_LINE


async def test_a_half_applied_book_is_found_unfinished_by_re_reading_and_the_retry_finishes_it(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("half_write", into=BOOK)
    async with at_booking_read_back(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id):
        await call.say("Yes.")

        assert faulty_adapter.requests[-3:] == ["POST /appointments", "GET /appointments", "POST /appointments"]
        assert call.tool_results("book_appointment") == [{"outcome": "succeeded"}]
        [appointment] = ehr.appointments_of(patient_id)
        assert appointment["slot"] == [{"reference": f"Slot/{slot_id}"}]
        assert ehr.slot_status(slot_id) == "busy"
        assert "booked" in call.agent_lines[-1]


async def test_a_book_half_applied_twice_frees_its_slot_hands_off_and_says_it_was_not_booked(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("half_write", into=BOOK, times=2)
    async with at_booking_read_back(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id):
        reply = await call.say("Yes.")

        assert ehr.appointments_of(patient_id) == []
        assert ehr.slot_status(slot_id) == "free"
        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert "could not book" in filed.reason.lower()
        assert "the slot is not held" in filed.reason.lower()
        assert "wasn't able to book" in reply
        assert call.ended


async def test_a_book_the_ehr_applies_after_the_agent_stopped_waiting_is_never_reported_as_not_booked(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("stalled_write", into=BOOK)
    faulty_adapter.inject("server_error", into=BOOK)
    async with at_booking_read_back(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id):
        reply = await call.say("Yes.")
        # The adapter gave up on the stalled Book before the agent said anything, so by now it has had its chance to land.
        await asyncio.sleep(STALLED_WRITE_LANDS_AFTER_SECS + 2)

        assert "wasn't able to book" in reply
        assert ehr.appointments_of(patient_id) == []
        assert ehr.slot_status(slot_id) == "free"


async def test_when_the_re_read_fails_too_the_retry_with_the_same_key_finds_the_book_that_landed(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("timeout", into=BOOK)
    faulty_adapter.inject("server_error", into=LIST_APPOINTMENTS)
    async with at_booking_read_back(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id):
        await call.say("Yes.")

        assert faulty_adapter.sent(BOOK) == 2
        assert call.tool_results("book_appointment") == [{"outcome": "succeeded"}]
        [appointment] = ehr.appointments_of(patient_id)
        assert appointment["slot"] == [{"reference": f"Slot/{slot_id}"}]
        assert "booked" in call.agent_lines[-1]


async def test_a_book_that_can_never_be_confirmed_hands_off_without_saying_whether_it_was_booked(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("half_write", into=BOOK, times=2)
    faulty_adapter.inject("server_error", into=LIST_APPOINTMENTS, times=2)
    async with at_booking_read_back(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id):
        reply = await call.say("Yes.")

        assert call.tool_results("book_appointment") == [{"outcome": "unknown"}]
        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert "could not confirm" in filed.reason.lower()
        assert "couldn't confirm whether your appointment was booked" in reply
        assert "call you back" in reply
        assert call.ended


@asynccontextmanager
async def at_cancel_read_back(ehr, start_call, adapter_url):
    """A Verified Patient's call, stopped at the Read-back of a Cancel. Yields (call, appointment_id, slot_id)."""
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    appointment_id = ehr.create_appointment(patient_id, faraday, clinic_time(1, "09:00"), "sick_visit")
    slot_id = ehr.appointment(appointment_id)["slot"][0]["reference"].removeprefix("Slot/")
    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("list_appointments"),
            CallTool("choose_appointment_to_cancel", {"appointment_id": appointment_id}),
            answer_read_back("yes"),
            CallTool("cancel_appointment"),
        ],
        adapter_url=adapter_url,
    ) as call:
        await call.converse(["I need to cancel my appointment.", f"Rosalind Okonkwo, {spoken(born)}."])
        assert call.state == "cancel_read_back"
        yield call, appointment_id, slot_id


async def test_a_half_applied_cancel_is_found_unfinished_by_re_reading_and_the_retry_frees_the_slot(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("half_write", into=CANCEL)
    async with at_cancel_read_back(ehr, start_call, faulty_adapter.url) as (call, appointment_id, slot_id):
        await call.say("Yes.")

        assert faulty_adapter.sent(CANCEL) == 2
        assert call.tool_results("cancel_appointment") == [{"outcome": "succeeded"}]
        assert ehr.appointment(appointment_id)["status"] == "cancelled"
        assert ehr.slot_status(slot_id) == "free"
        assert "cancelled" in call.agent_lines[-1]


async def test_a_cancel_that_timed_out_but_landed_is_found_by_re_reading_and_not_sent_again(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("timeout", into=CANCEL)
    async with at_cancel_read_back(ehr, start_call, faulty_adapter.url) as (call, appointment_id, slot_id):
        await call.say("Yes.")

        assert faulty_adapter.sent(CANCEL) == 1
        assert call.tool_results("cancel_appointment") == [{"outcome": "succeeded"}]
        assert ehr.slot_status(slot_id) == "free"
        assert "cancelled" in call.agent_lines[-1]


async def test_a_cancel_that_fails_twice_hands_off_and_leaves_the_appointment_booked(ehr, start_call, faulty_adapter):
    faulty_adapter.inject("server_error", into=CANCEL, times=2)
    async with at_cancel_read_back(ehr, start_call, faulty_adapter.url) as (call, appointment_id, slot_id):
        reply = await call.say("Yes.")

        assert ehr.appointment(appointment_id)["status"] == "booked"
        assert ehr.slot_status(slot_id) == "busy"
        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert "could not cancel" in filed.reason.lower()
        assert "wasn't able to cancel" in reply
        assert "cancelled" not in reply
        assert call.ended


@asynccontextmanager
async def at_reschedule_read_back(ehr, start_call, adapter_url):
    """A Verified Patient's call, stopped at the Read-back of a Reschedule.

    Yields (call, appointment_id, old_slot, new_slot). Another free Slot an hour later is there to offer instead.
    """
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    appointment_id = ehr.create_appointment(patient_id, faraday, clinic_time(1, "09:00"), "follow_up")
    old_slot = ehr.appointment(appointment_id)["slot"][0]["reference"].removeprefix("Slot/")
    new_slot = ehr.create_slot(faraday, clinic_time(2, "10:00"))
    ehr.create_slot(faraday, clinic_time(2, "11:00"))
    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("list_appointments"),
            CallTool("choose_appointment_to_reschedule", {"appointment_id": appointment_id}),
            CallTool("find_slots", {"provider": faraday.name}),
            "Dr. Faraday has 10 AM or 11 AM. Which would you like?",
            CallTool("choose_slot", {"slot_id": new_slot}),
            answer_read_back("yes"),
            CallTool("reschedule_appointment"),
            "Dr. Faraday still has 11 AM. Would that work?",
        ],
        adapter_url=adapter_url,
    ) as call:
        await call.converse(
            ["I need to move my appointment to any time with Dr. Faraday.", f"Rosalind Okonkwo, {spoken(born)}.", "10 AM."]
        )
        assert call.state == "reschedule_read_back"
        yield call, appointment_id, old_slot, new_slot


async def test_a_half_applied_reschedule_is_found_unfinished_by_re_reading_and_the_retry_finishes_it(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("half_write", into=RESCHEDULE)
    async with at_reschedule_read_back(ehr, start_call, faulty_adapter.url) as (call, appointment_id, old_slot, new_slot):
        await call.say("Yes.")

        assert faulty_adapter.sent(RESCHEDULE) == 2
        assert call.tool_results("reschedule_appointment") == [{"outcome": "succeeded"}]
        assert ehr.appointment(appointment_id)["slot"] == [{"reference": f"Slot/{new_slot}"}]
        assert ehr.slot_status(old_slot) == "free"
        assert ehr.slot_status(new_slot) == "busy"
        assert "is now on" in call.agent_lines[-1]


async def test_a_reschedule_half_applied_twice_frees_the_new_slot_and_leaves_the_appointment_where_it_was(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("half_write", into=RESCHEDULE, times=2)
    async with at_reschedule_read_back(ehr, start_call, faulty_adapter.url) as (call, appointment_id, old_slot, new_slot):
        reply = await call.say("Yes.")

        assert ehr.appointment(appointment_id)["slot"] == [{"reference": f"Slot/{old_slot}"}]
        assert ehr.slot_status(old_slot) == "busy"
        assert ehr.slot_status(new_slot) == "free"
        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert "could not move" in filed.reason.lower()
        assert "the new slot is not held" in filed.reason.lower()
        assert "wasn't able to move" in reply
        assert call.ended


async def test_a_reschedule_that_timed_out_but_landed_is_found_by_re_reading_and_not_sent_again(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("timeout", into=RESCHEDULE)
    async with at_reschedule_read_back(ehr, start_call, faulty_adapter.url) as (call, appointment_id, old_slot, new_slot):
        await call.say("Yes.")

        assert faulty_adapter.sent(RESCHEDULE) == 1
        assert call.tool_results("reschedule_appointment") == [{"outcome": "succeeded"}]
        assert ehr.appointment(appointment_id)["slot"] == [{"reference": f"Slot/{new_slot}"}]
        assert "is now on" in call.agent_lines[-1]


async def test_a_reschedule_that_fails_twice_hands_off_and_leaves_the_appointment_where_it_was(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("server_error", into=RESCHEDULE, times=2)
    async with at_reschedule_read_back(ehr, start_call, faulty_adapter.url) as (call, appointment_id, old_slot, new_slot):
        reply = await call.say("Yes.")

        assert ehr.appointment(appointment_id)["slot"] == [{"reference": f"Slot/{old_slot}"}]
        assert ehr.slot_status(new_slot) == "free"
        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert "could not move" in filed.reason.lower()
        assert "wasn't able to move" in reply
        assert "is now on" not in reply
        assert call.ended


async def test_a_new_time_taken_just_before_the_reschedule_is_explained_and_other_times_offered(
    ehr, start_call, faulty_adapter
):
    faulty_adapter.inject("slot_taken", into=RESCHEDULE)
    async with at_reschedule_read_back(ehr, start_call, faulty_adapter.url) as (call, appointment_id, old_slot, new_slot):
        reply = await call.say("Yes.")

        [rejected] = call.tool_results("reschedule_appointment")
        assert rejected["outcome"] == "rejected"
        assert [slot["time"].endswith("11 AM") for slot in rejected["slots"]] == [True]
        assert "that time was just taken" in reply
        assert "11 AM" in reply
        assert ehr.appointment(appointment_id)["slot"] == [{"reference": f"Slot/{old_slot}"}]
        assert ehr.callback_requests_from(call.caller_phone) == []
        assert not call.ended


async def test_a_slot_taken_just_before_the_book_is_explained_and_not_retried(ehr, start_call, faulty_adapter):
    faulty_adapter.inject("slot_taken", into=BOOK)
    async with at_booking_read_back(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id):
        reply = await call.say("Yes.")

        assert faulty_adapter.sent(BOOK) == 1
        assert call.tool_results("book_appointment")[0]["outcome"] == "rejected"
        assert "that time was just taken" in reply
        assert ehr.appointments_of(patient_id) == []
        assert call.state == "find_slot"
