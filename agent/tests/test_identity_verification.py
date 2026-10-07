"""Identity Verification, driven through the text transport against the real adapter and HAPI."""

from datetime import date

from fakes import CallTool

from clinic_agent.conversation import GREETING


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
