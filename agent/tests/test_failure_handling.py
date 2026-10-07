"""EHR failures during a write, injected by the adapter, driven through the text transport.

The agent retries a failed write once with the same idempotency key, re-reads the EHR after an
unknown one before saying anything, and hands off with a Callback Request when it still can't
confirm the write. It never says a write is done unless the EHR shows it done.
"""

from contextlib import asynccontextmanager

import pytest
from ehr import clinic_time
from fakes import CallTool
from faulty_adapter import BOOK
from scripts import spoken, verify

pytestmark = pytest.mark.scripted_only


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
