"""A call without caller ID, such as one from the browser: the Caller gives the number staff call back."""

import random

import pytest
from scripts import confirm_number, emergency_redirect, give_number, handoff

from clinic_agent.escalation import ASK_FOR_CALLBACK_NUMBER, EMERGENCY_REDIRECT


def _unused_phone() -> tuple[str, str]:
    """A number as the Caller says it, and as the Callback Request holds it."""
    digits = f"555{random.randrange(10**7):07d}"
    return f"{digits[:3]} {digits[3:6]} {digits[6:]}", f"+1{digits}"


async def test_a_handoff_without_caller_id_files_the_number_the_caller_gives_once_they_confirm_it(ehr, start_call):
    said, phone = _unused_phone()

    async with start_call(
        [handoff("asked_for_person"), give_number(said), confirm_number(True)], caller_phone=phone, caller_id=False
    ) as call:
        asked = await call.say("Can I talk to a real person?")
        assert asked == ASK_FOR_CALLBACK_NUMBER
        assert not call.ended

        read_back = await call.say(f"Sure, it's {said}.")
        assert f"{phone[2:5]}-{phone[5:8]}-{phone[8:]}" in read_back
        assert ehr.callback_requests_from(phone) == []

        await call.say("Yes, that's right.")

        [filed] = ehr.callback_requests_from(phone)
        assert "asked to speak to a person" in filed.reason.lower()
        assert not filed.emergency
        assert "call you back" in call.agent_lines[-1]
        assert call.ended
        assert ehr.callback_requests_from("unknown") == []


async def test_a_callback_number_the_caller_says_is_wrong_is_asked_for_again(ehr, start_call):
    misheard, wrong_phone = _unused_phone()
    said, phone = _unused_phone()

    async with start_call(
        [
            handoff("asked_for_person"),
            give_number(misheard),
            confirm_number(False),
            give_number(said),
            confirm_number(True),
        ],
        caller_phone=phone,
        caller_id=False,
    ) as call:
        await call.converse(["Can I talk to a real person?", f"It's {said}.", "No, that's not it.", f"{said}.", "Yes."])

        assert ehr.callback_requests_from(wrong_phone) == []
        [filed] = ehr.callback_requests_from(phone)
        assert "asked to speak to a person" in filed.reason.lower()
        assert call.ended


@pytest.mark.scripted_only
async def test_something_that_is_not_a_phone_number_is_never_filed(ehr, start_call):
    said, phone = _unused_phone()

    async with start_call(
        [handoff("asked_for_person"), give_number("12345"), "Sorry, could you say the whole number?"],
        caller_phone=phone,
        caller_id=False,
    ) as call:
        await call.converse(["Can I talk to a real person?", "One two three four five."])

        assert call.tool_results("record_callback_number") == [
            {"status": "error", "error": "That is not a 10-digit phone number. Ask for it again."}
        ]
        assert call.state == "callback_number"
        assert not call.ended


async def test_an_emergency_without_caller_id_says_911_first_then_files_the_number_given_at_once(ehr, start_call):
    said, phone = _unused_phone()

    async with start_call([emergency_redirect(), give_number(said)], caller_phone=phone, caller_id=False) as call:
        await call.say("My husband is having chest pain and he can't breathe!")

        # The greeting, then the 911 line before anything else.
        assert call.agent_lines[1] == EMERGENCY_REDIRECT
        assert "dial 911" in call.agent_lines[2]
        assert ehr.callback_requests_from(phone) == []

        await call.say(f"It's {said}.")

        [filed] = ehr.callback_requests_from(phone)
        assert filed.emergency
        assert "dial 911" in call.agent_lines[-1]
        assert call.ended
