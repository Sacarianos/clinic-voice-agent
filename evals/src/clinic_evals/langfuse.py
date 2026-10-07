"""Pushes eval scores to Langfuse Cloud.

Each run becomes one trace, sent as a single OpenTelemetry span: tagged with the LLM config and the
scenario, in a session for the whole eval batch. Each grader's verdict is a boolean score on that
trace, with the reason as its comment. Transcripts stay in the local results file.

This uses its own exporter call rather than the process-wide tracer, which belongs to the agent's
call tracing. Langfuse's older batch ingestion API is being retired, so it isn't used.
"""

import json
import secrets
from collections.abc import Mapping
from datetime import datetime

import httpx

from clinic_agent.tracing import DEFAULT_LANGFUSE_BASE_URL
from clinic_evals.graders import Grade


class LangfuseError(RuntimeError):
    pass


class Langfuse:
    def __init__(self, base_url: str, public_key: str, secret_key: str, *, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), auth=(public_key, secret_key), transport=transport, timeout=30
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Langfuse | None":
        """None when the keys aren't set."""
        public_key, secret_key = env.get("LANGFUSE_PUBLIC_KEY"), env.get("LANGFUSE_SECRET_KEY")
        if not (public_key and secret_key):
            return None
        return cls(env.get("LANGFUSE_BASE_URL") or DEFAULT_LANGFUSE_BASE_URL, public_key, secret_key)

    def push_run(
        self,
        *,
        batch_id: str,
        config: str,
        scenario: str,
        repeat: int,
        ending: str,
        grades: list[Grade],
        started: datetime,
        finished: datetime,
    ) -> str:
        """Sends one run as a trace with a score per grader. Returns the trace id."""
        trace_id = secrets.token_hex(16)
        attributes = {
            "langfuse.trace.name": f"eval {scenario}",
            "langfuse.session.id": batch_id,
            "langfuse.trace.tags": ["eval", config, scenario],
            "langfuse.trace.metadata.config": config,
            "langfuse.trace.metadata.scenario": scenario,
            "langfuse.trace.metadata.repeat": str(repeat),
            "langfuse.trace.metadata.ending": ending,
            "langfuse.trace.output": json.dumps({grade.grader: grade.passed for grade in grades}),
        }
        span = {
            "traceId": trace_id,
            "spanId": secrets.token_hex(8),
            "name": f"eval {scenario}",
            "kind": 1,
            "startTimeUnixNano": str(int(started.timestamp() * 1e9)),
            "endTimeUnixNano": str(int(finished.timestamp() * 1e9)),
            "attributes": [{"key": key, "value": _otel_value(value)} for key, value in attributes.items()],
        }
        self._send_span(span)
        for grade in grades:
            score = {
                "traceId": trace_id,
                "name": grade.grader,
                "value": 1 if grade.passed else 0,
                "dataType": "BOOLEAN",
                "comment": grade.reason,
                "metadata": {"config": config, "scenario": scenario},
            }
            self._http.post("/api/public/scores", json=score).raise_for_status()
        return trace_id

    def _send_span(self, span: dict) -> None:
        body = {
            "resourceSpans": [
                {
                    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "clinic-evals"}}]},
                    "scopeSpans": [{"scope": {"name": "clinic-evals"}, "spans": [span]}],
                }
            ]
        }
        response = self._http.post("/api/public/otel/v1/traces", json=body)
        response.raise_for_status()
        rejected = (response.json() or {}).get("partialSuccess", {}).get("rejectedSpans")
        if rejected:
            raise LangfuseError(f"Langfuse rejected the run's trace: {response.json()['partialSuccess']}")

    def close(self) -> None:
        self._http.close()


def _otel_value(value: str | list[str]) -> dict:
    if isinstance(value, list):
        return {"arrayValue": {"values": [{"stringValue": item} for item in value]}}
    return {"stringValue": value}
