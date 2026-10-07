"""Rescheduling and cancelling, driven through the text transport against the real adapter and HAPI."""

import pytest
from ehr import clinic_time
from fakes import CallTool
from scripts import spoken, verify


def spoken_day(when) -> str:
    return f"{when:%A}, {when:%B} {when.day}"


async def test_a_verified_patient_cancels_their_appointment_after_a_read_back_and_a_yes(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    nine = clinic_time(1, "09:00")
    appointment_id = ehr.create_appointment(patient_id, faraday, nine, "sick_visit")
    slot_id = ehr.appointment(appointment_id)["slot"][0]["reference"].removeprefix("Slot/")

    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("list_appointments"),
            "You have a sick visit with Dr. Faraday tomorrow at 9 AM. Is that the one to cancel?",
            CallTool("choose_appointment_to_cancel", {"appointment_id": appointment_id}),
            CallTool("cancel_appointment"),
        ]
    ) as call:
        await call.converse(
            [
                "Hi, I need to cancel my appointment.",
                f"Rosalind Okonkwo, {spoken(born)}.",
                "Yes, that one.",
            ]
        )

        read_back = call.agent_lines[-1]
        assert call.state == "cancel_read_back"
        for detail in ["cancel", faraday.name, "sick visit", spoken_day(nine), "9 AM"]:
            assert detail in read_back
        assert ehr.appointment(appointment_id)["status"] == "booked"

        await call.say("Yes, please.")

        assert call.tool_results("cancel_appointment") == [{"outcome": "succeeded"}]
        assert ehr.appointment(appointment_id)["status"] == "cancelled"
        assert ehr.slot_status(slot_id) == "free"
        assert "cancelled" in call.agent_lines[-1]
        assert call.state == "intent"
        assert not call.ended


async def test_a_verified_patient_reschedules_their_appointment_after_a_read_back_and_a_yes(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    nine, two_pm = clinic_time(1, "09:00"), clinic_time(2, "14:00")
    appointment_id = ehr.create_appointment(patient_id, faraday, nine, "annual_physical")
    old_slot = ehr.appointment(appointment_id)["slot"][0]["reference"].removeprefix("Slot/")
    new_slot = ehr.create_slot(faraday, two_pm)
    day = two_pm.date().isoformat()

    async with start_call(
        [
            "Of course. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("list_appointments"),
            "You have an annual physical with Dr. Faraday tomorrow at 9 AM. When would you like to move it to?",
            CallTool("choose_appointment_to_reschedule", {"appointment_id": appointment_id}),
            CallTool(
                "find_slots",
                {"provider": faraday.name, "from_date": day, "to_date": day, "part_of_day": "afternoon"},
            ),
            "Dr. Faraday has 2 PM that day. Would that work?",
            CallTool("choose_slot", {"slot_id": new_slot}),
            CallTool("reschedule_appointment"),
        ]
    ) as call:
        await call.converse(
            [
                "Hi, I need to move my appointment.",
                f"Rosalind Okonkwo, {spoken(born)}.",
                f"Could I come in on {spoken_day(two_pm)} afternoon instead?",
                "2 PM is perfect.",
            ]
        )

        read_back = call.agent_lines[-1]
        assert call.state == "reschedule_read_back"
        for detail in ["move", "annual physical", spoken_day(nine), "9 AM", spoken_day(two_pm), "2 PM", faraday.name]:
            assert detail in read_back
        assert ehr.slot_status(new_slot) == "free"

        await call.say("Yes.")

        assert call.tool_results("reschedule_appointment") == [{"outcome": "succeeded"}]
        [appointment] = ehr.appointments_of(patient_id)
        assert appointment["id"] == appointment_id
        assert appointment["status"] == "booked"
        assert appointment["slot"] == [{"reference": f"Slot/{new_slot}"}]
        assert ehr.slot_status(old_slot) == "free"
        assert ehr.slot_status(new_slot) == "busy"
        assert f"{spoken_day(two_pm)} at 2 PM" in call.agent_lines[-1]
        assert call.state == "intent"


async def test_with_two_upcoming_appointments_the_agent_asks_which_one_before_acting(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    sooner, later = clinic_time(1, "09:00"), clinic_time(3, "15:00")
    sick_visit = ehr.create_appointment(patient_id, faraday, sooner, "sick_visit")
    follow_up = ehr.create_appointment(patient_id, faraday, later, "follow_up")

    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("list_appointments"),
            "I see two: a sick visit tomorrow at 9 AM and a follow-up at 3 PM later this week. Which one?",
            CallTool("choose_appointment_to_cancel", {"appointment_id": follow_up}),
            CallTool("cancel_appointment"),
        ]
    ) as call:
        await call.converse(["I'd like to cancel my appointment.", f"Rosalind Okonkwo, {spoken(born)}."])

        [listed] = call.tool_results("list_appointments")
        assert [a["appointment_id"] for a in listed["appointments"]] == [sick_visit, follow_up]
        assert call.state == "choose_appointment"
        assert all(a["status"] == "booked" for a in ehr.appointments_of(patient_id))

        await call.say("The follow-up, please.")

        assert call.state == "cancel_read_back"
        assert "follow-up" in call.agent_lines[-1]
        assert f"{spoken_day(later)} at 3 PM" in call.agent_lines[-1]

        await call.say("Yes, cancel it.")

        assert ehr.appointment(follow_up)["status"] == "cancelled"
        assert ehr.appointment(sick_visit)["status"] == "booked"


NOT_AVAILABLE = "The function `{}` is not currently available."


@pytest.mark.scripted_only
async def test_cancel_is_unreachable_until_a_read_back_and_cancels_only_the_appointment_read_back(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    sick_visit = ehr.create_appointment(patient_id, faraday, clinic_time(1, "09:00"), "sick_visit")
    follow_up = ehr.create_appointment(patient_id, faraday, clinic_time(3, "15:00"), "follow_up")
    someone_else = ehr.create_patient(given="Theodora", family="Abernathy", birth_date=ehr.unused_birth_date())
    not_theirs = ehr.create_appointment(someone_else, faraday, clinic_time(2, "10:00"), "sick_visit")

    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("cancel_appointment"),
            CallTool("reschedule_appointment"),
            CallTool("list_appointments"),
            CallTool("cancel_appointment"),
            CallTool("choose_appointment_to_cancel", {"appointment_id": not_theirs}),
            "You have a sick visit tomorrow and a follow-up later this week. Which one?",
            CallTool("choose_appointment_to_cancel", {"appointment_id": sick_visit}),
            CallTool("choose_appointment_to_cancel", {"appointment_id": follow_up}),
            CallTool("cancel_appointment"),
        ]
    ) as call:
        await call.converse(
            [
                "Cancel my appointment, whatever it is.",
                f"Rosalind Okonkwo, {spoken(born)}.",
                "The sick visit.",
            ]
        )
        assert call.tool_results("cancel_appointment") == [NOT_AVAILABLE.format("cancel_appointment")] * 2
        assert call.tool_results("reschedule_appointment") == [NOT_AVAILABLE.format("reschedule_appointment")]
        assert call.tool_results("choose_appointment_to_cancel")[0]["status"] == "not_listed"
        assert "sick visit" in call.agent_lines[-1]
        assert all(a["status"] == "booked" for a in ehr.appointments_of(patient_id))

        await call.say("Oh no, sorry, I mean the follow-up.")
        assert "follow-up" in call.agent_lines[-1]

        await call.say("Yes.")

        assert ehr.appointment(follow_up)["status"] == "cancelled"
        assert ehr.appointment(sick_visit)["status"] == "booked"
        assert ehr.appointment(not_theirs)["status"] == "booked"


@pytest.mark.scripted_only
async def test_reschedule_is_unreachable_until_a_read_back_and_moves_only_to_the_time_read_back(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    appointment_id = ehr.create_appointment(patient_id, faraday, clinic_time(1, "09:00"), "follow_up")
    ten_slot = ehr.create_slot(faraday, clinic_time(2, "10:00"))
    eleven_slot = ehr.create_slot(faraday, clinic_time(2, "11:00"))

    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("list_appointments"),
            "You have a follow-up tomorrow at 9 AM. When would you like to move it to?",
            CallTool("choose_appointment_to_reschedule", {"appointment_id": appointment_id}),
            CallTool("find_slots", {"provider": faraday.name}),
            CallTool("reschedule_appointment"),
            "Dr. Faraday has 10 AM or 11 AM. Which would you like?",
            CallTool("choose_slot", {"slot_id": ten_slot}),
            CallTool("choose_slot", {"slot_id": eleven_slot}),
            CallTool("reschedule_appointment"),
        ]
    ) as call:
        await call.converse(
            [
                "I need to move my appointment.",
                f"Rosalind Okonkwo, {spoken(born)}.",
                "Just move it to whenever Dr. Faraday is free.",
                "10 AM.",
            ]
        )
        assert call.tool_results("reschedule_appointment") == [NOT_AVAILABLE.format("reschedule_appointment")]
        assert call.state == "reschedule_read_back"
        assert "10 AM" in call.agent_lines[-1]
        assert ehr.slot_status(ten_slot) == "free"

        await call.say("Hmm, make it 11 instead.")
        assert "11 AM" in call.agent_lines[-1]

        await call.say("Yes.")

        assert ehr.appointment(appointment_id)["slot"] == [{"reference": f"Slot/{eleven_slot}"}]
        assert ehr.slot_status(ten_slot) == "free"
        assert ehr.slot_status(eleven_slot) == "busy"
        assert not any("book_appointment" in tools for tools in call.offered_tools)


@pytest.mark.scripted_only
async def test_a_new_time_taken_before_the_yes_is_not_claimed_as_moved_and_new_times_are_offered(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    appointment_id = ehr.create_appointment(patient_id, faraday, clinic_time(1, "09:00"), "follow_up")
    old_slot = ehr.appointment(appointment_id)["slot"][0]["reference"].removeprefix("Slot/")
    ten_slot = ehr.create_slot(faraday, clinic_time(2, "10:00"))
    ehr.create_slot(faraday, clinic_time(2, "11:00"))

    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("list_appointments"),
            CallTool("choose_appointment_to_reschedule", {"appointment_id": appointment_id}),
            CallTool("find_slots", {"provider": faraday.name}),
            "Dr. Faraday has 10 AM or 11 AM. Which would you like?",
            CallTool("choose_slot", {"slot_id": ten_slot}),
            CallTool("reschedule_appointment"),
            "Dr. Faraday still has 11 AM. Would that work?",
        ]
    ) as call:
        await call.converse(
            [
                "I need to move my appointment to any time with Dr. Faraday.",
                f"Rosalind Okonkwo, {spoken(born)}.",
                "10 AM.",
            ]
        )
        ehr.take_slot(ten_slot)

        reply = await call.say("Yes.")

        [rejected] = call.tool_results("reschedule_appointment")
        assert rejected["outcome"] == "rejected"
        assert [slot["time"] for slot in rejected["slots"]] == [f"{spoken_day(clinic_time(2, '11:00'))} at 11 AM"]
        assert "I'm sorry, that time was just taken." in reply  # a slow EHR adds the holding line first
        assert call.state == "find_slot"
        assert ehr.appointment(appointment_id)["slot"] == [{"reference": f"Slot/{old_slot}"}]
        assert ehr.slot_status(old_slot) == "busy"
