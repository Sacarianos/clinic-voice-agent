"""Handoff: the call ends and a Callback Request reaches clinic staff, with the Caller's number and a reason."""

import re

import pytest
from scripts import handoff, spoken, verify

from clinic_agent.escalation import COULD_NOT_FILE_CALLBACK_REQUEST


@pytest.mark.parametrize(
    ("caller_says", "reason", "filed_reason"),
    [
        ("Can I talk to a real person?", "asked_for_person", "asked to speak to a person"),
        ("I'm calling for my mother, she needs a checkup.", "proxy_caller", "on behalf of someone else"),
        ("I've never been a patient there, I'd like to sign up.", "new_patient", "not a patient yet"),
        ("Is it safe to take ibuprofen with my blood pressure pills?", "clinical_question", "clinical question"),
    ],
    ids=["asks for a person", "Proxy Caller", "new patient", "clinical question"],
)
async def test_a_handoff_trigger_before_verification_files_a_callback_request_and_ends_the_call(
    ehr, start_call, caller_says, reason, filed_reason
):
    async with start_call([handoff(reason)]) as call:
        await call.say(caller_says)

        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert filed_reason in filed.reason.lower()
        assert not filed.emergency
        assert filed.patient_id is None
        assert "call you back" in call.agent_lines[-1]
        assert not [line for line in call.agent_lines if re.search(r"transfer|connect you|put you through", line, re.I)]
        assert call.ended
        assert call.state == "handoff"


async def test_a_clinical_question_from_a_verified_patient_hands_off_with_the_patient_linked(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)

    async with start_call(
        [
            "Happy to help. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            "Thank you, Rosalind. How can I help?",
            handoff("clinical_question"),
        ]
    ) as call:
        await call.converse(
            [
                "Hi, I have a question.",
                f"Rosalind Okonkwo, {spoken(born)}.",
                "Should I stop my medication before my blood test?",
            ]
        )

        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert "clinical question" in filed.reason.lower()
        assert filed.patient_id == patient_id
        assert call.ended


@pytest.mark.scripted_only
async def test_a_handoff_that_could_not_be_filed_never_promises_a_callback(start_call):
    nothing_listens_here = "http://127.0.0.1:9"

    async with start_call([handoff("asked_for_person")], adapter_url=nothing_listens_here) as call:
        await call.say("Can I talk to a real person?")

        assert call.agent_lines[-1] == COULD_NOT_FILE_CALLBACK_REQUEST
        assert "call you back" not in call.agent_lines[-1]
        assert call.ended
