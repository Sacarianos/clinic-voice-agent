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


async def test_a_proxy_caller_who_gives_someone_elses_details_is_handed_off_without_verifying_them(ehr, start_call):
    born = ehr.unused_birth_date()
    ehr.create_patient(given="Beatrix", family="Lindqvist", birth_date=born)

    async with start_call(
        [
            "I'd be happy to help. Can I get your mother's full name and date of birth?",
            verify("Beatrix", "Lindqvist", born, caller_is_the_patient=False),
        ]
    ) as call:
        await call.converse(
            [
                "Hi, I'm calling to book a checkup for my mother.",
                f"Her name is Beatrix Lindqvist, born {spoken(born)}.",
            ]
        )

        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert "on behalf of someone else" in filed.reason.lower()
        assert filed.patient_id is None
        assert {"status": "verified"} not in call.tool_results("verify_patient")
        assert not [tools for tools in call.offered_tools if "find_slots" in tools]
        assert call.ended
        assert call.state == "handoff"


async def test_a_patient_who_turns_out_to_be_calling_for_someone_else_is_handed_off_after_verification(
    ehr, start_call
):
    born = ehr.unused_birth_date()
    ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)

    async with start_call(
        [
            "Happy to help. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            "Thank you, Rosalind. How can I help?",
            handoff("proxy_caller"),
        ]
    ) as call:
        await call.converse(
            [
                "Hi, I need to book something.",
                f"Rosalind Okonkwo, {spoken(born)}.",
                "Actually the appointment isn't for me, it's for my husband. Can I book him in?",
            ]
        )

        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert "on behalf of someone else" in filed.reason.lower()
        assert not call.tool_results("book_appointment")
        assert call.ended
        assert call.state == "handoff"
