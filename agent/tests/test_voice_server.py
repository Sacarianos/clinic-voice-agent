import xml.etree.ElementTree as ET

from fakes import RecordingTTS, ScriptedLLM, SilentSTT
from starlette.testclient import TestClient

from clinic_agent.server import VoiceServices, create_app

CALL_SID = "CA00000000000000000000000000000001"
STREAM_SID = "MZ00000000000000000000000000000001"
GREETING = "Thanks for calling the clinic. How can I help you today?"


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


def test_caller_hears_the_agent_greet_them_as_soon_as_the_call_connects():
    tts = RecordingTTS()
    app = create_app(lambda: VoiceServices(stt=SilentSTT(), llm=ScriptedLLM([GREETING]), tts=tts))

    with TestClient(app) as client, client.websocket_connect("/ws") as twilio:
        _start_media_stream(twilio)
        audio = _next_media_message(twilio)
        twilio.send_json({"event": "stop", "streamSid": STREAM_SID, "stop": {"callSid": CALL_SID}})

    assert audio["streamSid"] == STREAM_SID
    assert audio["media"]["payload"]
    assert " ".join(sentence.strip() for sentence in tts.spoken) == GREETING


def _start_media_stream(twilio):
    """The two messages Twilio sends when a <Stream> opens."""
    twilio.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
    twilio.send_json(
        {
            "event": "start",
            "sequenceNumber": "1",
            "streamSid": STREAM_SID,
            "start": {
                "streamSid": STREAM_SID,
                "callSid": CALL_SID,
                "accountSid": "AC00000000000000000000000000000001",
                "tracks": ["inbound"],
                "customParameters": {},
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            },
        }
    )


def _next_media_message(twilio):
    while True:
        message = twilio.receive_json()
        if message["event"] == "media":
            return message
