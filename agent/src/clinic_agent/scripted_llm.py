"""A fake LLM for the text transport: it answers each LLM run with the next step of a script.

Conversation tests and the eval harness's own tests use it to drive the real flow with no API key.
"""

import uuid
from dataclasses import dataclass, field

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    FunctionCallFromLLM,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings
from pipecat.utils.tracing.service_decorators import traced_llm


@dataclass(frozen=True)
class CallTool:
    """A scripted LLM step that calls a tool instead of speaking."""

    name: str
    arguments: dict = field(default_factory=dict)


class ScriptedLLM(LLMService):
    """Answers each LLM run with the next scripted step: a line to speak or a CallTool.

    Like a real LLM service, it logs the context it was given at DEBUG level and traces each run as an
    `llm` span holding the context and its reply, so logs and traces carry what they would on a real call.
    """

    def __init__(self, steps: list[str | CallTool], *, usage: LLMTokenUsage | None = None):
        super().__init__(
            settings=LLMSettings(
                model="scripted",
                system_instruction=None,
                temperature=None,
                max_tokens=None,
                top_p=None,
                top_k=None,
                frequency_penalty=None,
                presence_penalty=None,
                seed=None,
                filter_incomplete_user_turns=None,
                user_turn_completion_config=None,
            )
        )
        self.steps = list(steps)
        self._usage = usage  # what each run reports as its token usage, when set

    def can_generate_metrics(self) -> bool:
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return
        await self._process_context(frame.context)

    @traced_llm
    async def _process_context(self, context: LLMContext):
        logger.debug(f"{self}: Generating chat from context {self.get_llm_adapter().get_messages_for_logging(context)}")
        step = self.steps.pop(0) if self.steps else "(script exhausted)"
        await self.push_frame(LLMFullResponseStartFrame())
        if isinstance(step, CallTool):
            await self.run_function_calls(
                [
                    FunctionCallFromLLM(
                        function_name=step.name,
                        tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
                        arguments=step.arguments,
                        context=context,
                    )
                ]
            )
        else:
            await self.push_frame(LLMTextFrame(step))
        if self._usage:
            await self.start_llm_usage_metrics(self._usage)
        await self.push_frame(LLMFullResponseEndFrame())
