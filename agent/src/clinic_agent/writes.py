"""Seeing a write through when the EHR misbehaves: Book, Reschedule and Cancel all go through here.

The adapter answers every write with one of four outcomes. Succeeded and rejected are final. Failed
means nothing was written, so the write is sent once more with the same idempotency key. Unknown
means it may or may not have landed, in full or in part, so the agent reads the EHR again: done
counts as succeeded, and anything less as failed. The adapter finishes a half-applied write when
the same write comes again, so the retry is safe either way.

A write that came back unknown may still be on its way to the EHR, or have left its Slot held. So
before such a write is given up as failed, it is released. A Book or Reschedule's Slot is freed if
the write holds it, and changed so the write can never land later. A Cancel's Appointment is changed
the same way. Only then is the Caller told nothing was done.
"""

from collections.abc import Awaitable, Callable

import httpx

from clinic_agent.audit import AuditLog, Write
from clinic_agent.ehr import WriteOutcome
from clinic_agent.escalation import FILING_ATTEMPTS, HandoffReason
from clinic_agent.timeouts import tool_timeout

ATTEMPTS = 2

# Each is_done reads the EHR at most this often. Cancel's reads twice, Book's and Reschedule's once.
MAX_RECONCILE_READS = 2

# The slowest write: every attempt sent and re-read, then released, and the Callback Request filed.
WRITE_TOOL_TIMEOUT_SECS = tool_timeout(ATTEMPTS * (1 + MAX_RECONCILE_READS) + 1 + FILING_ATTEMPTS)


async def write_until_settled(
    audit_log: AuditLog,
    write: Write,
    send: Callable[[], Awaitable[WriteOutcome]],
    is_done: Callable[[], Awaitable[bool]],
    release: Callable[[], Awaitable[WriteOutcome]] | None = None,
) -> WriteOutcome:
    """Sends the write, and once more unless it settled. Each call of `send` must send the same idempotency key.

    is_done reads the EHR and says whether everything the write was for is in place. release makes sure
    the write never lands later: EhrAdapter.release_slot for a Book or Reschedule, and
    EhrAdapter.release_from_cancel for a Cancel. Every attempt is recorded in the audit log exactly once,
    including one that raises or is cancelled.
    """
    answered_unknown = False
    for attempt in range(1, ATTEMPTS + 1):
        sent: WriteOutcome | None = None
        reconciled: WriteOutcome | None = None
        error: str | None = None
        try:
            sent = await send()
            if sent.outcome == "unknown":
                answered_unknown = True
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
    if outcome.outcome == "failed" and answered_unknown and release:
        return await _released(audit_log, write, release)
    return outcome


async def _reconcile(is_done: Callable[[], Awaitable[bool]]) -> WriteOutcome:
    try:
        return WriteOutcome("succeeded" if await is_done() else "failed")
    except httpx.HTTPError:
        return WriteOutcome("unknown")


async def _released(audit_log: AuditLog, write: Write, release: Callable[[], Awaitable[WriteOutcome]]) -> WriteOutcome:
    """What a write that seemed to fail after an unknown answer turns out to be, once it is released."""
    released: WriteOutcome | None = None
    error: str | None = None
    try:
        released = await release()
    except httpx.HTTPError as failure:
        error = type(failure).__name__
    except BaseException as raised:
        error = type(raised).__name__
        raise
    finally:
        audit_log.record(
            Write(
                "release_from_cancel" if write.action == "cancel" else "release_slot",
                write.patient_id,
                write.idempotency_key,
                appointment_id=write.appointment_id if write.action == "cancel" else None,
                slot_id=write.slot_id,
            ),
            attempt=1,
            outcome=released.outcome if released else "error",
            reason=(released.reason if released else None) or error,
        )
    if released and released.outcome == "succeeded":
        return WriteOutcome("failed")
    if released and released.reason == "write_landed":
        return WriteOutcome("succeeded")
    return WriteOutcome("unknown")


def unsettled(outcome: WriteOutcome, *, verb: str, done: str, details: str, slot: str | None = None) -> HandoffReason:
    """The Handoff for a write that still failed, or still can't be confirmed, after its retry.

    verb and done name the write, as in "book" and "booked". details say which appointment it was for.
    slot names the Slot the write would have taken, as in "the new Slot", for a write that takes one.
    """
    if outcome.outcome == "failed":
        slot_left = f" {slot[0].upper()}{slot[1:]} is not held for it." if slot else ""
        return HandoffReason(
            f"Could not {verb} an appointment ({details}). The EHR failed twice and nothing was {done}.{slot_left}",
            f"I'm sorry, I wasn't able to {verb} that appointment. "
            "A member of our staff will call you back to help. Goodbye.",
        )
    return HandoffReason(
        f"Could not confirm whether an appointment was {done} ({details}). Check the schedule before calling back.",
        f"I'm sorry, I couldn't confirm whether your appointment was {done}. "
        "A member of our staff will check and call you back. Goodbye.",
    )
