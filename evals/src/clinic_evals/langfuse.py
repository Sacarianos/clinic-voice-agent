"""Pushes eval scores to Langfuse Cloud through its public ingestion API.

Each run becomes one trace, tagged with the LLM config and the scenario, in a session for the whole
eval batch. Each grader's verdict is a boolean score on that trace, with the reason as its comment.
Transcripts stay in the local results file.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

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
        self, *, batch_id: str, config: str, scenario: str, repeat: int, ending: str, grades: list[Grade]
    ) -> str:
        """Sends one run as a trace with a score per grader. Returns the trace id."""
        trace_id = uuid.uuid4().hex
        trace = {
            "id": trace_id,
            "timestamp": _now(),
            "name": f"eval {scenario}",
            "sessionId": batch_id,
            "tags": ["eval", config, scenario],
            "metadata": {"config": config, "scenario": scenario, "repeat": repeat, "ending": ending},
            "output": {grade.grader: grade.passed for grade in grades},
        }
        scores = [
            {
                "id": uuid.uuid4().hex,
                "traceId": trace_id,
                "name": grade.grader,
                "value": 1 if grade.passed else 0,
                "dataType": "BOOLEAN",
                "comment": grade.reason,
                "metadata": {"config": config, "scenario": scenario},
            }
            for grade in grades
        ]
        self.ingest([_event("trace-create", trace), *(_event("score-create", score) for score in scores)])
        return trace_id

    def ingest(self, events: list[dict]) -> None:
        response = self._http.post("/api/public/ingestion", json={"batch": events})
        response.raise_for_status()
        errors = response.json().get("errors", [])
        if errors:
            raise LangfuseError(f"Langfuse refused {len(errors)} of {len(events)} events: {errors}")

    def close(self) -> None:
        self._http.close()


def _event(kind: str, body: dict) -> dict:
    return {"id": uuid.uuid4().hex, "type": kind, "timestamp": _now(), "body": body}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
