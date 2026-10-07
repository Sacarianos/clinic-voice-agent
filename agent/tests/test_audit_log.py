"""The audit log: one entry per write attempt, succeeded or not, with no patient data or transcript text.

Calls run through the real conversation against the real adapter, with faults injected into the writes.
"""

import json
import uuid
from contextlib import asynccontextmanager

import pytest
from ehr import clinic_time
from fakes import CallTool
from faulty_adapter import BOOK, CANCEL, LIST_APPOINTMENTS, RESCHEDULE
from scripts import answer_read_back, spoken, verify

pytestmark = pytest.mark.scripted_only

ENTRY_FIELDS = {
    "time",
    "action",
    "patient_id",
    "idempotency_key",
    "attempt",
    "outcome",
    "reason",
    "reconciled",
    "appointment_id",
    "slot_id",
}


def entries_for(audit_log, patient_id: str) -> list[dict]:
    return [entry for entry in audit_log.entries() if entry["patient_id"] == patient_id]


def is_uuid(value) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (TypeError, ValueError):
        return False


@asynccontextmanager
async def booking(ehr, start_call, adapter_url):
    """A Verified Patient's call, stopped at the Read-back of a Book. Yields (call, patient_id, slot_id, caller lines)."""
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    slot_id = ehr.create_slot(faraday, clinic_time(1, "09:00"))
    lines = ["I'd like an appointment.", f"Rosalind Okonkwo, {spoken(born)}.", "9 AM, a sick visit."]
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
        await call.converse(lines)
        yield call, patient_id, slot_id, [*lines, "Yes."]


async def test_a_book_retried_after_a_server_error_has_one_entry_per_attempt_under_one_key(
    ehr, start_call, faulty_adapter, audit_log
):
    faulty_adapter.inject("server_error", into=BOOK)
    async with booking(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id, _):
        await call.say("Yes.")

    first, second = entries_for(audit_log, patient_id)
    assert set(first) == ENTRY_FIELDS
    assert (first["action"], first["attempt"], first["outcome"], first["reconciled"]) == ("book", 1, "failed", None)
    assert (second["action"], second["attempt"], second["outcome"]) == ("book", 2, "succeeded")
    assert is_uuid(first["idempotency_key"])
    assert first["idempotency_key"] == second["idempotency_key"]
    assert first["slot_id"] == second["slot_id"] == slot_id
    assert first["time"] <= second["time"]


async def test_a_book_half_applied_twice_has_both_attempts_and_the_callback_request_it_filed(
    ehr, start_call, faulty_adapter, audit_log
):
    faulty_adapter.inject("half_write", into=BOOK, times=2)
    async with booking(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id, _):
        await call.say("Yes.")
        assert call.ended

    entries = entries_for(audit_log, patient_id)
    assert [(e["action"], e["attempt"], e["outcome"], e["reconciled"]) for e in entries] == [
        ("book", 1, "unknown", "failed"),
        ("book", 2, "unknown", "failed"),
        ("callback_request", 1, "succeeded", None),
    ]
    assert entries[2]["idempotency_key"] is None


async def test_a_cancel_that_timed_out_has_one_entry_that_says_re_reading_found_it_done(
    ehr, start_call, faulty_adapter, audit_log
):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    appointment_id = ehr.create_appointment(patient_id, faraday, clinic_time(1, "09:00"), "sick_visit")
    faulty_adapter.inject("timeout", into=CANCEL)
    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("list_appointments"),
            CallTool("choose_appointment_to_cancel", {"appointment_id": appointment_id}),
            answer_read_back("yes"),
            CallTool("cancel_appointment"),
        ],
        adapter_url=faulty_adapter.url,
    ) as call:
        await call.converse(["I need to cancel my appointment.", f"Rosalind Okonkwo, {spoken(born)}.", "Yes."])

    [entry] = entries_for(audit_log, patient_id)
    assert (entry["action"], entry["attempt"], entry["outcome"], entry["reconciled"]) == (
        "cancel",
        1,
        "unknown",
        "succeeded",
    )
    assert entry["appointment_id"] == appointment_id
    assert faulty_adapter.sent(LIST_APPOINTMENTS) == 2  # once to choose it, once to re-read


async def test_a_reschedule_rejected_because_the_new_slot_was_taken_has_one_entry_with_the_reason(
    ehr, start_call, faulty_adapter, audit_log
):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    appointment_id = ehr.create_appointment(patient_id, faraday, clinic_time(1, "09:00"), "annual_physical")
    two_pm = clinic_time(2, "14:00")
    new_slot = ehr.create_slot(faraday, two_pm)
    faulty_adapter.inject("slot_taken", into=RESCHEDULE)
    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("list_appointments"),
            CallTool("choose_appointment_to_reschedule", {"appointment_id": appointment_id}),
            CallTool("find_slots", {"provider": faraday.name, "from_date": two_pm.date().isoformat()}),
            "Dr. Faraday has 2 PM that day. Would that work?",
            CallTool("choose_slot", {"slot_id": new_slot}),
            answer_read_back("yes"),
            CallTool("reschedule_appointment"),
        ],
        adapter_url=faulty_adapter.url,
    ) as call:
        await call.converse(
            ["I need to move my appointment.", f"Rosalind Okonkwo, {spoken(born)}.", "2 PM is perfect.", "Yes."]
        )

    [entry] = entries_for(audit_log, patient_id)
    assert (entry["action"], entry["outcome"], entry["reason"]) == ("reschedule", "rejected", "slot_taken")
    assert (entry["appointment_id"], entry["slot_id"]) == (appointment_id, new_slot)


async def test_the_audit_log_holds_no_names_dates_of_birth_or_anything_the_caller_said(
    ehr, start_call, faulty_adapter, audit_log
):
    faulty_adapter.inject("half_write", into=BOOK, times=2)
    async with booking(ehr, start_call, faulty_adapter.url) as (call, patient_id, slot_id, lines):
        await call.say("Yes.")
        born = call.tool_calls[0].arguments["date_of_birth"]

    recorded = [json.dumps(entry) for entry in entries_for(audit_log, patient_id)]
    assert len(recorded) == 3
    text = "\n".join(recorded).casefold()
    for forbidden in ["rosalind", "okonkwo", born, spoken(born).casefold(), call.caller_phone, *map(str.casefold, lines)]:
        assert forbidden not in text
