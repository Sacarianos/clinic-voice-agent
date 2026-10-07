import base64
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fakes import RecordingTTS, ScriptedLLM, SilentSTT
from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from starlette.testclient import TestClient
from twilio_stream import CALL_SID, hang_up, next_media_message, start_media_stream

from clinic_agent.server import VoiceServices, create_app
from clinic_agent.tracing import configure_tracing


class FakeLangfuse:
    """Accepts OTLP/HTTP exports the way Langfuse Cloud does and keeps them."""

    def __init__(self):
        self.exports: list[tuple[str, str, bytes]] = []
        received = self.exports

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.path, self.headers["Authorization"], body))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def spans(self) -> list:
        spans = []
        for _, _, body in self.exports:
            request = ExportTraceServiceRequest.FromString(body)
            for resource_spans in request.resource_spans:
                for scope_spans in resource_spans.scope_spans:
                    spans.extend(scope_spans.spans)
        return spans

    def wait_for_span(self, name: str, timeout: float = 10.0):
        """A span is exported only once it ends, and the conversation ends after the hang-up is processed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            trace.get_tracer_provider().force_flush()
            matching = [span for span in self.spans() if span.name == name]
            if matching:
                [span] = matching
                return span
            time.sleep(0.1)
        raise AssertionError(f"no {name!r} span reached Langfuse within {timeout}s")

    def close(self):
        self._server.shutdown()


@pytest.fixture
def langfuse():
    server = FakeLangfuse()
    yield server
    # Flush and stop the exporter while the server can still answer it.
    shutdown = getattr(trace.get_tracer_provider(), "shutdown", None)
    if shutdown:
        shutdown()
    server.close()


def test_tracing_stays_off_without_langfuse_keys():
    assert configure_tracing({}) is False


def test_a_call_is_traced_to_langfuse_as_one_conversation_keyed_by_the_call_sid(langfuse):
    # configure_tracing installs the process-wide tracer provider, so only this test turns it on.
    env = {
        "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
        "LANGFUSE_SECRET_KEY": "sk-lf-test",
        "LANGFUSE_BASE_URL": langfuse.url,
    }
    assert configure_tracing(env) is True
    app = create_app(
        lambda: VoiceServices(stt=SilentSTT(), llm=ScriptedLLM(["Hello."]), tts=RecordingTTS()),
        tracing=True,
    )

    with TestClient(app) as client, client.websocket_connect("/ws") as twilio:
        start_media_stream(twilio)
        next_media_message(twilio)
        hang_up(twilio)
        conversation = langfuse.wait_for_span("conversation")

    basic_auth = "Basic " + base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
    assert {(path, auth) for path, auth, _ in langfuse.exports} == {("/api/public/otel/v1/traces", basic_auth)}
    attributes = {a.key: a.value.string_value for a in conversation.attributes}
    assert attributes["conversation.id"] == CALL_SID
    assert attributes["langfuse.session.id"] == CALL_SID
