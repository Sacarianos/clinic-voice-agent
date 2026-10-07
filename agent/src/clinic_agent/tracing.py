"""Sends Pipecat's OpenTelemetry spans to Langfuse Cloud over OTLP/HTTP."""

import base64
from collections.abc import Mapping

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from pipecat.utils.tracing.setup import setup_tracing

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
    return setup_tracing(service_name="clinic-voice-agent", exporter=exporter)
