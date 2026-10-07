"""The audit log: one append-only record per attempt to write to the EHR, whatever came of it.

A record says who the write was for (patient id), what it was (action, idempotency key, and the
Appointment and Slot ids), when it was sent and what the EHR answered. It never holds names, dates of
birth, phone numbers or anything the Caller or the agent said, so a reviewer can reconstruct every
write without reading a transcript.

Records are JSON lines appended to one file. Nothing in the agent rewrites or deletes them.
"""

import json
import os
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

DEFAULT_AUDIT_LOG_PATH = "audit-log.jsonl"

Action = Literal["book", "reschedule", "cancel", "callback_request", "emergency_callback_request"]


@dataclass(frozen=True)
class Write:
    """What a write is for. Every attempt at it is recorded under these details."""

    action: Action
    patient_id: str | None
    idempotency_key: str | None = None
    appointment_id: str | None = None
    slot_id: str | None = None


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "AuditLog":
        return cls(Path(env.get("AUDIT_LOG_PATH") or DEFAULT_AUDIT_LOG_PATH))

    def record(
        self,
        write: Write,
        *,
        attempt: int,
        outcome: str,
        reason: str | None = None,
        reconciled: str | None = None,
    ) -> None:
        """One attempt at `write`.

        outcome is what the EHR adapter answered (succeeded, rejected, failed or unknown), or "error" when
        the attempt raised, with the error's type as the reason. reason is a code such as slot_taken, never
        free text. reconciled is what re-reading the EHR found after an unknown answer.
        """
        entry = {
            "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
            **asdict(write),
            "attempt": attempt,
            "outcome": outcome,
            "reason": reason,
            "reconciled": reconciled,
        }
        line = json.dumps(entry) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(line)
                file.flush()
                os.fsync(file.fileno())

    def entries(self) -> list[dict]:
        """Every record, oldest first."""
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as file:
            return [json.loads(line) for line in file if line.strip()]
