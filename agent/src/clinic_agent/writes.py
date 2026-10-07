"""Seeing a write through when the EHR misbehaves: Book, Reschedule and Cancel all go through here.

The adapter answers every write with one of four outcomes. Succeeded and rejected are final. Failed
means nothing was written, so the write is sent once more with the same idempotency key. Unknown
means it may or may not have landed, in full or in part, so the agent reads the EHR again: done
counts as succeeded, and anything less as failed. The adapter finishes a half-applied write when
the same write comes again, so the retry is safe either way.
"""

from collections.abc import Awaitable, Callable

import httpx

from clinic_agent.audit import AuditLog, Write
from clinic_agent.ehr import WriteOutcome
from clinic_agent.escalation import HandoffReason

ATTEMPTS = 2

# Pipecat abandons a tool that runs past its timeout, and a write must never be abandoned mid-way.
# The worst case is two attempts and their re-reads, each up to the adapter client's 5 s (a Cancel
# re-reads twice), and then filing the Callback Request (two attempts of 5 s).
WRITE_TOOL_TIMEOUT_SECS = 45


async def write_until_settled(
    audit_log: AuditLog,
    write: Write,
    send: Callable[[], Awaitable[WriteOutcome]],
    is_done: Callable[[], Awaitable[bool]],
) -> WriteOutcome:
    """Sends the write, and once more unless it settled. Each call of `send` must send the same idempotency key.

    is_done reads the EHR and says whether everything the write was for is in place. Every attempt is
    recorded in the audit log exactly once, including one that raises or is cancelled.
    """
    for attempt in range(1, ATTEMPTS + 1):
        sent: WriteOutcome | None = None
        reconciled: WriteOutcome | None = None
        error: str | None = None
        try:
            sent = await send()
            if sent.outcome == "unknown":
                reconciled = await _reconcile(is_done)
        except BaseException as raised:
            error = type(raised).__name__
            raise
        finally:
            audit_log.record(
                write,
                attempt=attempt,
                outcome=sent.outcome if sent else "error",
                reason=(sent.reason if sent else None) or error,
                reconciled=reconciled.outcome if reconciled else None,
            )
        outcome = reconciled or sent
        if outcome.outcome in ("succeeded", "rejected"):
            return outcome
    return outcome


async def _reconcile(is_done: Callable[[], Awaitable[bool]]) -> WriteOutcome:
    try:
        return WriteOutcome("succeeded" if await is_done() else "failed")
    except httpx.HTTPError:
        return WriteOutcome("unknown")


def unsettled(outcome: WriteOutcome, *, verb: str, done: str, details: str) -> HandoffReason:
    """The Handoff for a write that still failed, or still can't be confirmed, after its retry.

    verb and done name the write, as in "book" and "booked". details say which appointment it was for.
    """
    if outcome.outcome == "failed":
        return HandoffReason(
            f"Could not {verb} an appointment ({details}). The EHR failed twice and nothing was {done}.",
            f"I'm sorry, I wasn't able to {verb} that appointment. "
            "A member of our staff will call you back to help. Goodbye.",
        )
    return HandoffReason(
        f"Could not confirm whether an appointment was {done} ({details}). Check the schedule before calling back.",
        f"I'm sorry, I couldn't confirm whether your appointment was {done}. "
        "A member of our staff will check and call you back. Goodbye.",
    )
