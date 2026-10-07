"""Identity Verification, driven through the text transport against the real adapter and HAPI."""

import pytest
from fakes import CallTool
from scripts import spoken, verify

from clinic_agent.conversation import (
    GREETING,
    HANDOFF_AFTER_FAILED_VERIFICATION,
    SPELL_LAST_NAME,
    VERIFICATION_FAILED,
)


async def test_a_patient_who_gives_their_name_and_date_of_birth_is_verified_and_reaches_intent(ehr, start_call):
    born = ehr.unused_birth_date()
    ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)

    async with start_call(
        [
            "I can help with that. First, what is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            "Thank you, Rosalind. What would you like to book?",
        ]
    ) as call:
        assert call.agent_lines == [GREETING]
        await call.say("Hi, I'd like to book an appointment.")
        await call.say(f"Rosalind Okonkwo, born {spoken(born)}.")

        assert call.tool_results("verify_patient") == [{"status": "verified"}]
        assert call.state == "intent"
        assert not call.ended


@pytest.mark.parametrize(
    ("given_name", "family_name", "wrong_date_of_birth"),
    [("Theodora", "Abernathy", True), ("Bartholomew", "Quigley", False)],
    ids=["wrong date of birth", "unknown name"],
)
async def test_a_failed_attempt_gets_the_same_failure_message_whatever_was_wrong(
    ehr, start_call, given_name, family_name, wrong_date_of_birth
):
    born = ehr.unused_birth_date()
    ehr.create_patient(given="Theodora", family="Abernathy", birth_date=born)
    stated_birth_date = ehr.unused_birth_date() if wrong_date_of_birth else born

    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify(given_name, family_name, stated_birth_date),
        ]
    ) as call:
        await call.say("I need to move my appointment.")
        await call.say(f"{given_name} {family_name}, {spoken(stated_birth_date)}.")

        assert call.tool_results("verify_patient") == [{"status": "not_verified"}]
        assert call.agent_lines[-1] == VERIFICATION_FAILED
        assert call.state == "verify_identity"
        assert not call.ended


async def test_a_second_failed_attempt_ends_the_call_with_a_handoff_and_a_callback_request_for_nobody(ehr, start_call):
    born = ehr.unused_birth_date()
    ehr.create_patient(given="Theodora", family="Abernathy", birth_date=born)
    first_guess, second_guess = ehr.unused_birth_date(), ehr.unused_birth_date()

    async with start_call(
        [
            "Sure. What is your full name and date of birth?",
            verify("Theodora", "Abernathy", first_guess),
            verify("Theodora", "Abernathy", second_guess),
        ]
    ) as call:
        await call.converse(
            [
                "I need to cancel an appointment.",
                f"Theodora Abernathy, {spoken(first_guess)}.",
                f"Sorry, it's {spoken(second_guess)}.",
            ]
        )

        assert call.tool_results("verify_patient") == [{"status": "not_verified"}] * 2
        assert call.agent_lines[-1] == HANDOFF_AFTER_FAILED_VERIFICATION
        assert call.ended
        assert call.state != "intent"
        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert "verification failed" in filed.reason.lower()
        assert not filed.emergency
        assert filed.patient_id is None


async def test_a_name_that_matches_two_patients_gets_a_spelling_request_that_is_not_a_failed_attempt(ehr, start_call):
    born = ehr.unused_birth_date()
    ehr.create_patient(given="Joanna", family="Smith", birth_date=born)
    ehr.create_patient(given="Johanna", family="Smyth", birth_date=born)
    wrong_birth_date = ehr.unused_birth_date()

    async with start_call(
        [
            "Of course. Can I have your full name and date of birth?",
            verify("Joanna", "Smith", wrong_birth_date),
            verify("Joanna", "Smith", born),
            verify("Joanna", "SMITH", born),
            "Thanks, Joanna. Which day works for you?",
        ]
    ) as call:
        await call.say("I'd like to book a checkup.")
        await call.say(f"Joanna Smith, {spoken(wrong_birth_date)}.")
        await call.say(f"Oops, I mean {spoken(born)}.")

        # Joanna Smith is one Patient's exact name, but speech recognition can hear Johanna Smyth that way.
        assert call.agent_lines[-1] == SPELL_LAST_NAME
        assert not call.ended

        await call.say("S, M, I, T, H.")

        assert call.tool_results("verify_patient") == [
            {"status": "not_verified"},
            {"status": "ambiguous"},
            {"status": "verified"},
        ]
        assert call.state == "intent"


async def test_a_spelled_surname_that_still_matches_two_patients_ends_the_call_with_a_handoff(ehr, start_call):
    born = ehr.unused_birth_date()
    ehr.create_patient(given="Joanna", family="Smith", birth_date=born)
    ehr.create_patient(given="Johanna", family="Smith", birth_date=born)

    async with start_call(
        [
            "Of course. Can I have your full name and date of birth?",
            verify("Joanna", "Smith", born),
            verify("Joanna", "SMITH", born),
        ]
    ) as call:
        await call.converse(["I'd like to book a checkup.", f"Joanna Smith, {spoken(born)}.", "S, M, I, T, H."])

        assert call.tool_results("verify_patient") == [{"status": "ambiguous"}] * 2
        assert call.agent_lines[-1] == HANDOFF_AFTER_FAILED_VERIFICATION
        assert call.ended
        assert call.state != "intent"
        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert filed.patient_id is None
        assert not filed.emergency


@pytest.mark.scripted_only
async def test_a_verification_that_does_not_say_whose_details_they_are_verifies_nobody(ehr, start_call):
    born = ehr.unused_birth_date()
    ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)
    undeclared = CallTool(
        "verify_patient", {"given_name": "Rosalind", "family_name": "Okonkwo", "date_of_birth": born}
    )

    async with start_call(
        ["Sure. What is your full name and date of birth?", undeclared, "Are those your own details?"]
    ) as call:
        await call.converse(["I'd like to book an appointment.", f"Rosalind Okonkwo, {spoken(born)}."])

        assert [result["status"] for result in call.tool_results("verify_patient")] == ["error"]
        assert call.state == "verify_identity"
        assert not call.ended
