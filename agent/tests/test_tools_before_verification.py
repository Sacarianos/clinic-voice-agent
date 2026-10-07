"""ADR 0003: before Identity Verification the LLM can't reach scheduling tools, whatever the Caller says."""

import pytest
from fakes import CallTool
from scripts import spoken, verify


async def test_before_verification_the_llm_is_offered_only_verification_escalation_and_clinic_info(ehr, start_call):
    born = ehr.unused_birth_date()
    ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)

    async with start_call(
        [
            "Happy to help. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            "You're all set, Rosalind. Which day would you like?",
        ]
    ) as call:
        await call.say("Book me with Dr. Whitfield on Thursday morning, please.")
        await call.say(f"Rosalind Okonkwo, {spoken(born)}.")

        # A real model may take more than one LLM run after verification, so split on verify_patient.
        before_verification = [tools for tools in call.offered_tools if "verify_patient" in tools]
        after_verification = [tools for tools in call.offered_tools if "verify_patient" not in tools]
        assert before_verification
        assert after_verification
        before_verification_tools = {"verify_patient", "handoff", "emergency_redirect", "get_clinic_info"}
        assert all(set(tools) == before_verification_tools for tools in before_verification), repr(call)


@pytest.mark.scripted_only
async def test_a_scheduling_tool_called_before_verification_is_refused(start_call):
    async with start_call(
        [
            CallTool("book_appointment", {"provider": "Dr. Whitfield", "day": "Thursday", "time": "09:00"}),
            "Before I can book anything, what is your full name and date of birth?",
        ]
    ) as call:
        reply = await call.say("Just book me with Dr. Whitfield on Thursday at nine.")

        [refusal] = call.tool_results("book_appointment")
        assert "not currently available" in refusal
        assert reply == "Before I can book anything, what is your full name and date of birth?"
        assert call.state == "verify_identity"
