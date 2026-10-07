"""Identity Verification, driven through the text transport against the real adapter and HAPI."""

from datetime import date

import pytest
from fakes import CallTool

from clinic_agent.conversation import (
    GREETING,
    HANDOFF_AFTER_FAILED_VERIFICATION,
    SPELL_LAST_NAME,
    VERIFICATION_FAILED,
)


def spoken(iso_date: str) -> str:
    """A date of birth the way a Caller says it."""
    day = date.fromisoformat(iso_date)
    return f"{day:%B} {day.day}, {day.year}"


def verify(given_name: str, family_name: str, date_of_birth: str) -> CallTool:
    return CallTool(
        "verify_patient",
        {"given_name": given_name, "family_name": family_name, "date_of_birth": date_of_birth},
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


async def test_a_second_failed_attempt_ends_the_call_with_a_handoff_message(ehr, start_call):
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
        await call.say("I need to cancel an appointment.")
        await call.say(f"Theodora Abernathy, {spoken(first_guess)}.")
        await call.say(f"Sorry, it's {spoken(second_guess)}.")

        assert call.tool_results("verify_patient") == [{"status": "not_verified"}] * 2
        assert call.agent_lines[-1] == HANDOFF_AFTER_FAILED_VERIFICATION
        assert call.ended
        assert call.state != "intent"


async def test_a_name_that_matches_two_patients_gets_a_spelling_request_that_is_not_a_failed_attempt(ehr, start_call):
    born = ehr.unused_birth_date()
    ehr.create_patient(given="Joanna", family="Smith", birth_date=born)
    ehr.create_patient(given="Johanna", family="Smyth", birth_date=born)
    wrong_birth_date = ehr.unused_birth_date()

    async with start_call(
        [
            "Of course. Can I have your full name and date of birth?",
            verify("Jo", "Smithe", wrong_birth_date),
            verify("Jo", "Smithe", born),
            verify("Jo", "SMITH", born),
            "Thanks, Jo. Which day works for you?",
        ]
    ) as call:
        await call.say("I'd like to book a checkup.")
        await call.say(f"Jo Smithe, {spoken(wrong_birth_date)}.")
        await call.say(f"Oops, I mean {spoken(born)}.")

        assert call.agent_lines[-1] == SPELL_LAST_NAME
        assert not call.ended

        await call.say("S, M, I, T, H.")

        assert call.tool_results("verify_patient") == [
            {"status": "not_verified"},
            {"status": "ambiguous"},
            {"status": "verified"},
        ]
        assert call.state == "intent"
