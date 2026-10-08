import base64

from fakes import RecordingTTS, ScriptedLLM, SilentSTT
from starlette.testclient import TestClient
from twilio_stream import CALL_SID, hang_up, next_media_message, start_media_stream

from clinic_agent.ehr import EhrAdapter
from clinic_agent.server import TwilioAccount, create_app
from clinic_agent.services import VoiceServices
from clinic_agent.tracing import configure_tracing


def test_tracing_stays_off_without_langfuse_keys():
    assert configure_tracing({}) is False


def test_a_call_is_traced_to_langfuse_as_one_conversation_keyed_by_the_call_sid(langfuse):
    app = create_app(
        lambda: VoiceServices(
            stt=SilentSTT(), llm=ScriptedLLM([]), tts=RecordingTTS(), ehr=EhrAdapter("http://127.0.0.1:9")
        ),
        twilio=TwilioAccount(account_sid="AC00000000000000000000000000000001", auth_token="test-auth-token"),
        hang_up_through_twilio=False,
        tracing=True,
    )

    with TestClient(app) as client, client.websocket_connect("/ws") as twilio:
        start_media_stream(twilio)
        next_media_message(twilio)
        hang_up(twilio, app)
        conversation = langfuse.wait_for_span("conversation", CALL_SID)

    basic_auth = "Basic " + base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
    assert {(path, auth) for path, auth, _ in langfuse.exports} == {("/api/public/otel/v1/traces", basic_auth)}
    attributes = {a.key: a.value.string_value for a in conversation.attributes}
    assert attributes["conversation.id"] == CALL_SID
    assert attributes["langfuse.session.id"] == CALL_SID
