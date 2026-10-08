"""Clinic Questions: hours, address, parking and who does what come from static config, with or without verification."""

import pytest
from fakes import CallTool
from scripts import spoken, verify

GET_CLINIC_INFO = CallTool("get_clinic_info")


async def test_a_caller_who_has_not_verified_gets_the_clinics_hours_and_stays_unverified(ehr, start_call):
    async with start_call([GET_CLINIC_INFO, "We're open Monday through Friday, 8 to 5."]) as call:
        await call.say("Hi, what are your hours?")

        [info] = call.tool_results("get_clinic_info")
        assert "Monday through Friday" in info["hours"]
        assert "8" in info["hours"] and "5" in info["hours"]
        assert call.tool_results("verify_patient") == []
        assert call.state == "verify_identity"
        assert not call.ended


async def test_the_clinic_info_covers_address_parking_and_what_each_provider_does(ehr, start_call):
    async with start_call([GET_CLINIC_INFO, "Sure, here is what I have."]) as call:
        await call.say("Where are you, where do I park, and who should I see for a sore throat?")

        [info] = call.tool_results("get_clinic_info")
        assert set(info) == {"hours", "address", "parking", "providers"}
        assert info["address"]
        assert info["parking"]
        for provider in ("Whitfield", "Szczepanski", "Kowalczyk"):
            assert provider in info["providers"]


@pytest.mark.scripted_only  # a real model may answer the second question from the first lookup
async def test_a_verified_patient_gets_the_same_clinic_info_without_any_patient_data(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)

    async with start_call(
        [
            GET_CLINIC_INFO,
            "We're open Monday through Friday. Now, what is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            "Thank you, Rosalind. How can I help?",
            GET_CLINIC_INFO,
            "There is parking behind the building.",
        ]
    ) as call:
        await call.converse(
            ["What time do you open?", f"Rosalind Okonkwo, {spoken(born)}.", "Is there parking?"]
        )

        before, after = call.tool_results("get_clinic_info")
        assert before == after
        assert call.state == "intent"
        for fact in (str(before), str(after)):
            assert patient_id not in fact
            assert "Rosalind" not in fact
            assert "Okonkwo" not in fact


async def test_every_conversation_node_offers_clinic_info(ehr, start_call):
    born = ehr.unused_birth_date()
    ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)

    async with start_call(
        [
            "Happy to help. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            "Thank you, Rosalind. How can I help?",
        ]
    ) as call:
        await call.converse(["Hi, I need an appointment.", f"Rosalind Okonkwo, {spoken(born)}."])

        assert len(call.offered_tools) >= 2
        assert all("get_clinic_info" in tools for tools in call.offered_tools)
