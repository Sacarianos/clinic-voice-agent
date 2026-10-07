"""Seeing a write through when the EHR misbehaves: Book, Reschedule and Cancel all go through here.

The adapter answers every write with one of four outcomes. Succeeded and rejected are final. Failed
means nothing was written, so the write is sent once more with the same idempotency key.
"""

from collections.abc import Awaitable, Callable

from clinic_agent.ehr import WriteOutcome

ATTEMPTS = 2


async def write_until_settled(write: Callable[[], Awaitable[WriteOutcome]]) -> WriteOutcome:
    """Runs the write, and once more if it failed. Each call of `write` must send the same idempotency key."""
    for _ in range(ATTEMPTS):
        outcome = await write()
        if outcome.outcome in ("succeeded", "rejected"):
            return outcome
    return outcome
