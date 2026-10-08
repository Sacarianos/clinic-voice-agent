import time
import xml.etree.ElementTree as ET

from fakes import RecordingTTS, RunLLMOnceGreeted, ScriptedLLM, SilentSTT
from scripts import handoff
from starlette.testclient import TestClient
from twilio_stream import CALL_SID, STREAM_SID, hang_up, next_media_message, start_media_stream

from clinic_agent.conversation import GREETING
from clinic_agent.ehr import EhrAdapter
from clinic_agent.server import create_app
from clinic_agent.services import VoiceServices

# Greeting and waiting for the Caller never reach the EHR.
UNUSED_EHR = EhrAdapter("http://127.0.0.1:9")


def _no_services():
    raise AssertionError("the webhook must not build a pipeline")


def test_webhook_answers_with_twiml_that_streams_the_call_to_the_websocket():
    client = TestClient(create_app(_no_services))

    response = client.post(
        "/voice",
        data={"CallSid": CALL_SID, "From": "+15555550123", "To": "+15555550100"},
        headers={"host": "clinic-agent.ngrok-free.app"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    twiml = ET.fromstring(response.text)
    assert twiml.tag == "Response"
    [connect] = twiml
    assert connect.tag == "Connect"
    [stream] = connect
    assert stream.tag == "Stream"
    assert stream.get("url") == "wss://clinic-agent.ngrok-free.app/ws"
    parameters = {parameter.get("name"): parameter.get("value") for parameter in stream}
    assert parameters == {"from_number": "+15555550123"}


def test_caller_hears_the_agent_greet_them_as_soon_as_the_call_connects():
    tts = RecordingTTS()
    app = create_app(lambda: VoiceServices(stt=SilentSTT(), llm=ScriptedLLM([]), tts=tts, ehr=UNUSED_EHR))

    with TestClient(app) as client, client.websocket_connect("/ws") as twilio:
        start_media_stream(twilio)
        audio = next_media_message(twilio)
        hang_up(twilio)

    assert audio["streamSid"] == STREAM_SID
    assert audio["media"]["payload"]
    assert " ".join(sentence.strip() for sentence in tts.spoken) == GREETING


def test_a_callback_request_filed_on_a_phone_call_has_the_number_twilio_passed_as_a_stream_parameter(
    ehr, ehr_adapter_url
):
    phone = "+15555550188"
    app = create_app(
        lambda: VoiceServices(
            stt=RunLLMOnceGreeted(),
            llm=ScriptedLLM([handoff("asked_for_person")]),
            tts=RecordingTTS(),
            ehr=EhrAdapter(ehr_adapter_url),
        )
    )

    try:
        with TestClient(app) as client, client.websocket_connect("/ws") as twilio:
            start_media_stream(twilio, from_number=phone)
            filed = _wait_for_callback_request(ehr, phone)
            hang_up(twilio)
    finally:
        ehr.delete_callback_requests_from(phone)

    assert "asked to speak to a person" in filed.reason.lower()


def _wait_for_callback_request(ehr, phone, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if found := ehr.callback_requests_from(phone):
            return found[0]
        time.sleep(0.1)
    raise AssertionError(f"no Callback Request for {phone} within {seconds}s")
