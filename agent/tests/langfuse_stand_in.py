"""A local stand-in for Langfuse Cloud's OTLP/HTTP endpoint, so tests see exactly what tracing would upload."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


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

    def wait_for_span(self, name: str, conversation_id: str, timeout: float = 10.0):
        """A span is exported only once it ends, and the conversation ends after the hang-up is processed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            trace.get_tracer_provider().force_flush()
            matching = [
                span
                for span in self.spans()
                if span.name == name and _string_attributes(span).get("conversation.id") == conversation_id
            ]
            if matching:
                [span] = matching
                return span
            time.sleep(0.1)
        raise AssertionError(f"no {name!r} span for {conversation_id} reached Langfuse within {timeout}s")

    def close(self):
        self._server.shutdown()


def _string_attributes(span) -> dict[str, str]:
    return {a.key: a.value.string_value for a in span.attributes}
