"""Emergency Redirect: from any state the Caller is told to dial 911, the call ends and staff get an emergency Callback Request."""

from scripts import emergency_redirect, spoken, verify


async def test_an_emergency_before_verification_tells_the_caller_to_dial_911_and_files_an_emergency_callback_request(
    ehr, start_call
):
    async with start_call([emergency_redirect()]) as call:
        await call.say("My husband is having chest pain and he can't breathe!")

        assert "dial 911" in call.agent_lines[-1]
        assert call.ended
        assert call.state == "emergency_redirect"
        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert filed.emergency
        assert "emergency" in filed.reason.lower()
        assert filed.patient_id is None


async def test_an_emergency_after_verification_files_an_emergency_callback_request_for_the_patient(ehr, start_call):
    born = ehr.unused_birth_date()
    patient_id = ehr.create_patient(given="Rosalind", family="Okonkwo", birth_date=born)

    async with start_call(
        [
            "Happy to help. What is your full name and date of birth?",
            verify("Rosalind", "Okonkwo", born),
            "Thank you, Rosalind. How can I help?",
            emergency_redirect(),
        ]
    ) as call:
        await call.converse(
            [
                "Hi, I need an appointment.",
                f"Rosalind Okonkwo, {spoken(born)}.",
                "Actually, I think I'm having a stroke, my face is drooping.",
            ]
        )

        assert "dial 911" in call.agent_lines[-1]
        assert call.ended
        [filed] = ehr.callback_requests_from(call.caller_phone)
        assert filed.emergency
        assert filed.patient_id == patient_id


async def test_every_conversation_node_offers_handoff_and_emergency_redirect(ehr, start_call):
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

        assert call.state == "intent"
        assert len(call.offered_tools) >= 2
        assert all({"handoff", "emergency_redirect"} <= set(tools) for tools in call.offered_tools)
