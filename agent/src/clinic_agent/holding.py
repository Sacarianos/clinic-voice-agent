"""The holding line: what the Caller hears while a tool waits on the EHR, so the line never goes quiet."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from pipecat.flows import FlowManager
from pipecat.frames.frames import TTSSpeakFrame

HOLDING_LINE = "One moment while I check on that."
STILL_WORKING = "Thanks for waiting, I'm still working on it."

# Most EHR calls answer well within a second, and then nothing is said. A slow one gets the holding
# line, and a reminder every few seconds after it, since a write that retries can take a while.
HOLDING_LINE_AFTER_SECS = 1.0
STILL_WORKING_EVERY_SECS = 5.0

Handler = Callable[[dict, FlowManager], Awaitable[Any]]


@asynccontextmanager
async def holding_line(flow_manager: FlowManager) -> AsyncIterator[None]:
    """Speaks the holding line if the work inside takes longer than about a second."""

    async def speak_while_waiting():
        await asyncio.sleep(HOLDING_LINE_AFTER_SECS)
        await flow_manager.worker.queue_frame(TTSSpeakFrame(HOLDING_LINE))
        while True:
            await asyncio.sleep(STILL_WORKING_EVERY_SECS)
            await flow_manager.worker.queue_frame(TTSSpeakFrame(STILL_WORKING))

    speaking = asyncio.create_task(speak_while_waiting())
    try:
        yield
    finally:
        speaking.cancel()


def with_holding_line(handler: Handler) -> Handler:
    """A tool handler that speaks the holding line while it waits. For any tool that calls the EHR."""

    async def handle(args: dict, flow_manager: FlowManager):
        async with holding_line(flow_manager):
            return await handler(args, flow_manager)

    return handle
