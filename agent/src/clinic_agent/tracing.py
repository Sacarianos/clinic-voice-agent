"""Sends Pipecat's OpenTelemetry spans to Langfuse Cloud over OTLP/HTTP, with patient data masked."""

import base64
import json
from collections.abc import Mapping, Sequence
from typing import Any

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Status
from pipecat.utils.tracing.setup import setup_tracing

from clinic_agent.phi import PHI

DEFAULT_LANGFUSE_BASE_URL = "https://cloud.langfuse.com"


def configure_tracing(env: Mapping[str, str]) -> bool:
    """Install the process-wide tracer when Langfuse keys are set. Returns whether tracing is on."""
    public_key = env.get("LANGFUSE_PUBLIC_KEY")
    secret_key = env.get("LANGFUSE_SECRET_KEY")
    if not (public_key and secret_key):
        return False
    base_url = (env.get("LANGFUSE_BASE_URL") or DEFAULT_LANGFUSE_BASE_URL).rstrip("/")
    credentials = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    exporter = OTLPSpanExporter(
        endpoint=f"{base_url}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {credentials}"},
    )
    return setup_tracing(service_name="clinic-voice-agent", exporter=MaskingSpanExporter(exporter))


class MaskingSpanExporter(SpanExporter):
    """Masks patient data in every span before the wrapped exporter sends it.

    Pipecat's spans hold the raw conversation: STT transcripts, the LLM's input messages and output,
    tool calls and results, and the text sent to TTS. A span can't change once it has ended, so each one
    is rebuilt with its attributes, events and status masked.
    """

    def __init__(self, inner: SpanExporter):
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._inner.export([_masked(span) for span in spans])

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


def _masked(span: ReadableSpan) -> ReadableSpan:
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=_masked_attributes(span.attributes),
        events=[Event(event.name, _masked_attributes(event.attributes), event.timestamp) for event in span.events],
        links=span.links,
        kind=span.kind,
        status=Status(span.status.status_code, _masked_value(span.status.description)),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def _masked_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    return {key: _masked_value(value) for key, value in (attributes or {}).items()}


def _masked_value(value: Any) -> Any:
    if isinstance(value, str):
        return _masked_text(value)
    if isinstance(value, (list, tuple)):
        return [_masked_value(item) for item in value]
    return value


def _masked_text(text: str) -> str:
    """LLM input, output and tool definitions are JSON. Masking them as data keeps them valid JSON."""
    if text[:1] in ("{", "["):
        try:
            return json.dumps(PHI.mask_data(json.loads(text)))
        except ValueError:
            pass
    return PHI.mask(text)
