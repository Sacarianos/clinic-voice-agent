import time
import xml.etree.ElementTree as ET

import pytest
from fakes import RecordingTTS, RunLLMOnceGreeted, ScriptedLLM, SilentSTT
from scripts import handoff
from starlette.testclient import TestClient
from twilio_stream import CALL_SID, STREAM_SID, hang_up, next_media_message, start_media_stream

from clinic_agent.config import ConfigError
from clinic_agent.conversation import GREETING
from clinic_agent.ehr import EhrAdapter
from clinic_agent.server import TwilioAccount, app_from_env, create_app
from clinic_agent.services import VoiceServices

# Greeting and waiting for the Caller never reach the EHR.
UNUSED_EHR = EhrAdapter("http://127.0.0.1:9")

# Made-up credentials. Tests don't hang up through Twilio's REST API, so they never leave the process.
TWILIO = TwilioAccount(account_sid="AC00000000000000000000000000000001", auth_token="test-auth-token")

# What Twilio posts when a call comes in to https://clinic-agent.ngrok-free.app/voice, and the signature it
# sends with it. Twilio's own RequestValidator (twilio-python 9.11.2) computed the signature with TWILIO's
# auth token, so it doesn't depend on the server's code.
NGROK_HOST = "clinic-agent.ngrok-free.app"
INCOMING_CALL = {
    "AccountSid": "AC00000000000000000000000000000001",
    "CallSid": CALL_SID,
    "From": "+15555550123",
    "To": "+15555550100",
}
INCOMING_CALL_SIGNATURE = "yIkYZNIveOomrfPEebprLzicSBQ="

PHONE_KEYS = {"DEEPGRAM_API_KEY": "placeholder", "ANTHROPIC_API_KEY": "placeholder"}
TWILIO_KEYS = {"TWILIO_ACCOUNT_SID": TWILIO.account_sid, "TWILIO_AUTH_TOKEN": TWILIO.auth_token}


def _no_services():
    raise AssertionError("the webhook must not build a pipeline")


def _app(make_services):
    return create_app(make_services, twilio=TWILIO, hang_up_through_twilio=False)


def test_webhook_answers_with_twiml_that_streams_the_call_to_the_websocket():
    client = TestClient(_app(_no_services))

    # ngrok ends TLS and forwards plain HTTP, keeping the public host in the Host header.
    response = client.post(
        "/voice",
        data=INCOMING_CALL,
        headers={"host": NGROK_HOST, "x-twilio-signature": INCOMING_CALL_SIGNATURE},
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


@pytest.mark.parametrize(
    ("form", "headers"),
    [
        pytest.param(INCOMING_CALL, {"host": NGROK_HOST}, id="unsigned"),
        pytest.param(
            INCOMING_CALL,
            {"host": NGROK_HOST, "x-twilio-signature": "bm90IGEgc2lnbmF0dXJlIGF0IGFsbA=="},
            id="wrong-signature",
        ),
        pytest.param(
            {**INCOMING_CALL, "From": "+15555550199"},
            {"host": NGROK_HOST, "x-twilio-signature": INCOMING_CALL_SIGNATURE},
            id="form-changed-after-signing",
        ),
        pytest.param(
            INCOMING_CALL,
            {"host": "somewhere-else.example", "x-twilio-signature": INCOMING_CALL_SIGNATURE},
            id="signed-for-another-url",
        ),
    ],
)
def test_webhook_refuses_a_request_twilio_did_not_sign(form, headers):
    client = TestClient(_app(_no_services))

    response = client.post("/voice", data=form, headers=headers)

    assert response.status_code == 403
    assert "Stream" not in response.text


@pytest.mark.parametrize("missing", ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"])
def test_server_refuses_to_start_without_twilio_credentials(missing):
    env = {**PHONE_KEYS, **TWILIO_KEYS}
    del env[missing]

    with pytest.raises(ConfigError, match=missing):
        app_from_env(env)


def test_server_started_from_the_environment_checks_twilio_signatures():
    client = TestClient(app_from_env({**PHONE_KEYS, **TWILIO_KEYS}))

    signed = client.post(
        "/voice", data=INCOMING_CALL, headers={"host": NGROK_HOST, "x-twilio-signature": INCOMING_CALL_SIGNATURE}
    )
    unsigned = client.post("/voice", data=INCOMING_CALL, headers={"host": NGROK_HOST})

    assert signed.status_code == 200
    assert unsigned.status_code == 403


def test_caller_hears_the_agent_greet_them_as_soon_as_the_call_connects():
    tts = RecordingTTS()
    app = _app(lambda: VoiceServices(stt=SilentSTT(), llm=ScriptedLLM([]), tts=tts, ehr=UNUSED_EHR))

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
    app = _app(
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
