"""Seeing a write through when the EHR misbehaves: Book, Reschedule and Cancel all go through here.

The adapter answers every write with one of four outcomes. Succeeded and rejected are final. Failed
means nothing was written, so the write is sent once more with the same idempotency key.
"""

from collections.abc import Awaitable, Callable

from clinic_agent.ehr import WriteOutcome
from clinic_agent.escalation import HandoffReason

ATTEMPTS = 2


async def write_until_settled(write: Callable[[], Awaitable[WriteOutcome]]) -> WriteOutcome:
    """Runs the write, and once more if it failed. Each call of `write` must send the same idempotency key."""
    for _ in range(ATTEMPTS):
        outcome = await write()
        if outcome.outcome in ("succeeded", "rejected"):
            return outcome
    return outcome


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
