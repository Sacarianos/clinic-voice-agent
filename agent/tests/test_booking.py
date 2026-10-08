"""Booking an appointment, driven through the text transport against the real adapter and HAPI."""

import pytest
from ehr import clinic_time
from fakes import CallTool
from scripts import answer_read_back, own_tools, spoken, verify

NOT_AVAILABLE = "The function `{}` is not currently available."


def spoken_day(when) -> str:
    return f"{when:%A}, {when:%B} {when.day}"


@pytest.mark.parametrize(
    "caller_says",
    [
        "I need to see someone, I've been feeling sick.",
        "I've had a fever since Monday and I'd like to come in.",
        "My throat really hurts. Can I get an appointment this week?",
    ],
)
async def test_feeling_unwell_and_wanting_to_be_seen_is_a_booking_not_a_clinical_question(ehr, start_call, caller_says):
    async with start_call(
        ["Sorry to hear that. I can help you book a visit. What is your full name and date of birth?"]
    ) as call:
        await call.say(caller_says)

        assert call.tool_results("handoff") == []
        assert ehr.callback_requests_from(call.caller_phone) == []
        assert call.state == "verify_identity"
        assert not call.ended


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
            answer_read_back("yes"),
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
        [book] = [tool for tool in call.tool_calls if tool.name == "book_appointment"]
        said_before_the_result = [line for _, line in call.transcript[: book.transcript_position]]
        assert read_back in said_before_the_result
        assert call.agent_lines[-1] not in said_before_the_result
        assert call.tool_results("record_read_back_answer") == [{"answer": "yes"}]
        offering_book = [own_tools(tools) for tools in call.offered_tools if "book_appointment" in tools]
        assert offering_book
        assert all(tools == {"book_appointment"} for tools in offering_book)


@pytest.mark.scripted_only
async def test_a_no_to_the_read_back_goes_back_to_choosing_and_a_book_called_at_the_read_back_is_refused(
    ehr, start_call
):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    nine_slot = ehr.create_slot(faraday, clinic_time(1, "09:00"))
    ehr.create_slot(faraday, clinic_time(1, "09:30"))

    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("find_slots", {"provider": faraday.name}),
            "Dr. Faraday has 9 AM or 9:30 AM tomorrow. What is the visit for?",
            CallTool("choose_slot", {"slot_id": nine_slot, "visit_type": "sick_visit"}),
            # The Caller says no, and the LLM books anyway, then records the no and tries once more.
            CallTool("book_appointment"),
            answer_read_back("no"),
            CallTool("book_appointment"),
            "Sorry about that. Which time would you like instead?",
        ]
    ) as call:
        await call.converse(
            ["I'd like an appointment.", f"Rosalind Okonkwo, {spoken(born)}.", "9 AM, a sick visit."]
        )
        assert call.state == "read_back"
        at_read_back = len(call.offered_tools)

        await call.say("No, that's wrong.")

        assert own_tools(call.offered_tools[at_read_back]) == {"record_read_back_answer"}
        assert call.tool_results("book_appointment") == [NOT_AVAILABLE.format("book_appointment")] * 2
        assert call.tool_results("record_read_back_answer") == [{"answer": "no"}]
        assert call.state == "find_slot"
        assert call.agent_lines[-1] == "Sorry about that. Which time would you like instead?"
        assert ehr.appointments_of(patient_id) == []
        assert ehr.slot_status(nine_slot) == "free"


@pytest.mark.scripted_only
async def test_book_is_unreachable_until_a_read_back_and_books_only_the_details_read_back(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    nine_slot = ehr.create_slot(faraday, clinic_time(1, "09:00"))
    half_past_nine_slot = ehr.create_slot(faraday, clinic_time(1, "09:30"))

    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("book_appointment"),
            "Let me look for open times first. Who would you like to see?",
            CallTool("find_slots", {"provider": faraday.name}),
            CallTool("book_appointment"),
            "I have 9 AM or 9:30 AM. Which would you like, and what is the visit for?",
            CallTool("choose_slot", {"slot_id": nine_slot, "visit_type": "sick_visit"}),
            answer_read_back("change"),
            CallTool("book_appointment"),
            CallTool("choose_slot", {"slot_id": half_past_nine_slot, "visit_type": "follow_up"}),
            answer_read_back("yes"),
            CallTool("book_appointment"),
        ]
    ) as call:
        await call.converse(
            [
                "I'd like an appointment.",
                f"Rosalind Okonkwo, {spoken(born)}.",
                f"{faraday.name}. Just book me in, whatever time.",
                "Nine is fine, I'm sick.",
            ]
        )
        not_available = NOT_AVAILABLE.format("book_appointment")
        assert call.tool_results("book_appointment") == [not_available, not_available]
        assert call.state == "read_back"
        assert ehr.appointments_of(patient_id) == []

        await call.say("Actually, make it 9:30, and it's a follow-up.")
        assert call.tool_results("record_read_back_answer") == [{"answer": "change"}]
        assert call.tool_results("book_appointment") == [not_available] * 3
        assert ehr.appointments_of(patient_id) == []
        assert call.state == "read_back"
        assert "9:30 AM" in call.agent_lines[-1]
        assert "a follow-up" in call.agent_lines[-1]

        await call.say("Yes.")

        [appointment] = ehr.appointments_of(patient_id)
        assert appointment["slot"] == [{"reference": f"Slot/{half_past_nine_slot}"}]
        assert appointment["appointmentType"]["coding"][0]["code"] == "follow_up"
        assert ehr.slot_status(nine_slot) == "free"


async def test_asking_for_another_time_during_the_read_back_offers_new_slots_without_starting_over(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    morning, afternoon = clinic_time(1, "09:00"), clinic_time(1, "15:00")
    morning_slot = ehr.create_slot(faraday, morning)
    afternoon_slot = ehr.create_slot(faraday, afternoon)
    day = morning.date().isoformat()

    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            "Thanks, Rosalind. Who would you like to see, and when?",
            CallTool(
                "find_slots",
                {"provider": faraday.name, "from_date": day, "to_date": day, "part_of_day": "morning"},
            ),
            "Dr. Faraday has 9 AM. What is the visit for?",
            CallTool("choose_slot", {"slot_id": morning_slot, "visit_type": "sick_visit"}),
            answer_read_back("change"),
            CallTool(
                "find_slots",
                {"provider": faraday.name, "from_date": day, "to_date": day, "part_of_day": "afternoon"},
            ),
            "Dr. Faraday is free at 3 PM. Would that work?",
            CallTool("choose_slot", {"slot_id": afternoon_slot, "visit_type": "sick_visit"}),
            answer_read_back("yes"),
            CallTool("book_appointment"),
        ]
    ) as call:
        await call.converse(
            [
                "I need to see someone, I've been feeling sick.",
                f"Rosalind Okonkwo, {spoken(born)}.",
                f"{faraday.name}, {spoken_day(morning)}, in the morning.",
                "The 9 AM one, please. It's a sick visit.",
            ]
        )
        assert call.state == "read_back"

        await call.say("Hmm, actually, does she have anything that afternoon instead?")

        offered = [slot["slot_id"] for slot in call.tool_results("find_slots")[-1]["slots"]]
        assert offered == [afternoon_slot]

        await call.converse(["Yes, 3 PM, same reason.", "Yes, please book it."])

        assert len(call.tool_results("verify_patient")) == 1
        [appointment] = ehr.appointments_of(patient_id)
        assert appointment["slot"] == [{"reference": f"Slot/{afternoon_slot}"}]
        assert ehr.slot_status(morning_slot) == "free"


@pytest.mark.scripted_only
async def test_a_slot_taken_before_the_yes_is_not_claimed_as_booked_and_new_slots_are_offered(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    faraday = ehr.create_provider(given="Imogen", family="Faraday")
    nine_slot = ehr.create_slot(faraday, clinic_time(1, "09:00"))
    ehr.create_slot(faraday, clinic_time(1, "10:00"))

    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            CallTool("find_slots", {"provider": faraday.name}),
            "Dr. Faraday has 9 AM or 10 AM. What is the visit for?",
            CallTool("choose_slot", {"slot_id": nine_slot, "visit_type": "follow_up"}),
            answer_read_back("yes"),
            CallTool("book_appointment"),
            "Dr. Faraday still has 10 AM. Would that work?",
        ]
    ) as call:
        await call.converse(
            [
                "I'd like an appointment.",
                f"Rosalind Okonkwo, {spoken(born)}, with {faraday.name} please.",
                "9 AM, a follow-up.",
            ]
        )
        ehr.take_slot(nine_slot)

        reply = await call.say("Yes.")

        [rejected] = call.tool_results("book_appointment")
        assert rejected["outcome"] == "rejected"
        assert [slot["time"] for slot in rejected["slots"]] == [f"{spoken_day(clinic_time(1, '10:00'))} at 10 AM"]
        assert "I'm sorry, that time was just taken." in reply  # a slow EHR adds the holding line first
        assert "booked" not in reply
        assert call.state == "find_slot"
        assert ehr.appointments_of(patient_id) == []
