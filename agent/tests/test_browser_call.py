"""Browser calls: Pipecat's prebuilt page talks to the agent over WebRTC, through the same pipeline and flow as a phone call."""

import asyncio
import random
import time

from browser import Browser
from fakes import RecordingTTS, ScriptedCaller, ScriptedLLM, SilentSTT
from scripts import confirm_number, give_number, handoff
from starlette.testclient import TestClient

from clinic_agent.conversation import GREETING
from clinic_agent.ehr import EhrAdapter
from clinic_agent.escalation import ASK_FOR_CALLBACK_NUMBER
from clinic_agent.phi import PHI
from clinic_agent.server import create_app
from clinic_agent.services import VoiceServices

# Greeting and waiting for the Caller never reach the EHR.
UNUSED_EHR = EhrAdapter("http://127.0.0.1:9")


def _greeting_only():
    return VoiceServices(stt=SilentSTT(), llm=ScriptedLLM([]), tts=RecordingTTS(), ehr=UNUSED_EHR)


def test_the_server_sends_the_browser_to_the_prebuilt_page():
    client = TestClient(create_app(_greeting_only))

    page = client.get("/")

    assert page.status_code == 200
    assert page.url.path == "/client/"
    assert page.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in page.text


async def test_a_browser_caller_hears_the_agent_greet_them_as_soon_as_they_connect():
    tts = RecordingTTS()
    app = create_app(lambda: VoiceServices(stt=SilentSTT(), llm=ScriptedLLM([]), tts=tts, ehr=UNUSED_EHR))
    browser = Browser(app)

    try:
        await browser.connect()
        await browser.hear()
        await _until(lambda: " ".join(sentence.strip() for sentence in tts.spoken) == GREETING, "the greeting")
    finally:
        await browser.hang_up()

    assert PHI.mask("The write outcome was unknown.") == "The write outcome was unknown."


async def test_a_browser_caller_who_asks_for_a_person_is_called_back_at_the_number_they_give(ehr, ehr_adapter_url):
    digits = f"555{random.randrange(10**7):07d}"
    phone = f"+1{digits}"
    caller = ScriptedCaller(["Can I talk to a real person?", f"It's {digits}.", "Yes, that's right."])
    tts = RecordingTTS()
    app = create_app(
        lambda: VoiceServices(
            stt=caller,
            llm=ScriptedLLM([handoff("asked_for_person"), give_number(digits), confirm_number(True)]),
            tts=tts,
            ehr=EhrAdapter(ehr_adapter_url),
        )
    )
    browser = Browser(app)

    try:
        await browser.connect()
        [filed] = await _until(lambda: ehr.callback_requests_from(phone), "a Callback Request", seconds=20)
        # The agent says goodbye and hangs up.
        await browser.call_ended()
    finally:
        await browser.hang_up()
        ehr.delete_callback_requests_from(phone)

    assert caller.lines == []
    assert "asked to speak to a person" in filed.reason.lower()
    heard = " ".join(sentence.strip() for sentence in tts.spoken)
    assert heard.index(ASK_FOR_CALLBACK_NUMBER) < heard.index(f"I have {digits[:3]}-{digits[3:6]}-{digits[6:]}")


async def test_a_line_typed_on_the_page_before_verification_is_masked_whole_like_speech():
    llm = ScriptedLLM(["Thanks. And your date of birth?"])
    app = create_app(lambda: VoiceServices(stt=SilentSTT(), llm=llm, tts=RecordingTTS(), ehr=UNUSED_EHR))
    browser = Browser(app)
    line = "Hi, this is Philippa Quenneville and I need an appointment."

    try:
        await browser.connect()
        await browser.hear()
        await browser.type(line)
        await _until(lambda: not llm.steps, "the agent to answer the typed line")
    finally:
        await browser.hang_up()

    assert PHI.mask(f"user said: {line}") == "user said: [UNVERIFIED CALLER]"


async def test_a_browser_call_is_traced_to_langfuse_as_one_conversation_keyed_by_its_session(langfuse):
    app = create_app(_greeting_only, tracing=True)
    browser = Browser(app)

    try:
        await browser.connect()
        await browser.hear()
    finally:
        await browser.hang_up()
    conversation = await asyncio.to_thread(langfuse.wait_for_span, "conversation", browser.session_id)

    attributes = {a.key: a.value.string_value for a in conversation.attributes}
    assert attributes["langfuse.session.id"] == browser.session_id


async def _until(found, what, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if result := await asyncio.to_thread(found):
            return result
        await asyncio.sleep(0.1)
    raise AssertionError(f"no sign of {what} within {seconds}s")
