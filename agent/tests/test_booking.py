"""Booking an appointment, driven through the text transport against the real adapter and HAPI."""

from ehr import clinic_time
from fakes import CallTool
from scripts import spoken, verify


def spoken_day(when) -> str:
    return f"{when:%A}, {when:%B} {when.day}"


async def test_a_verified_patient_books_a_slot_after_a_read_back_and_a_yes(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    nine, half_past_nine = clinic_time(1, "09:00"), clinic_time(1, "09:30")
    nine_slot = ehr.create_slot(faraday, nine)
    ehr.create_slot(faraday, half_past_nine)

    async with start_call(
        [
            "I can help with that. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            "Thanks, Rosalind. Who would you like to see, and when?",
            CallTool(
                "find_slots",
                {"provider": faraday.name, "from_date": nine.date().isoformat(), "to_date": nine.date().isoformat()},
            ),
            "Dr. Faraday has 9 AM or 9:30 AM tomorrow. Which works, and what is the visit for?",
            CallTool("choose_slot", {"slot_id": nine_slot, "visit_type": "annual_physical"}),
            CallTool("book_appointment"),
        ]
    ) as call:
        await call.converse(
            [
                "Hi, I'd like to book an appointment.",
                f"Rosalind Okonkwo, {spoken(born)}.",
                f"With {faraday.name}, on {spoken_day(nine)} please.",
                "Nine o'clock, for my annual physical.",
            ]
        )

        read_back = call.agent_lines[-1]
        assert call.state == "read_back"
        for detail in [faraday.name, "annual physical", spoken_day(nine), "9 AM"]:
            assert detail in read_back
        assert ehr.appointments_of(patient_id) == []

        await call.say("Yes, that's right.")

        assert call.tool_results("book_appointment") == [{"outcome": "succeeded"}]
        [appointment] = ehr.appointments_of(patient_id)
        assert appointment["status"] == "booked"
        assert appointment["slot"] == [{"reference": f"Slot/{nine_slot}"}]
        assert appointment["appointmentType"]["coding"][0]["code"] == "annual_physical"
        assert ehr.slot_status(nine_slot) == "busy"
        assert "booked" in call.agent_lines[-1]
        assert not call.ended
