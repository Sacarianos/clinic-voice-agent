"""Stand-ins for Deepgram and the LLM so a call runs without network or API keys."""

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from pipecat.frames.frames import (
    Frame,
    FunctionCallFromLLM,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    LLMSetToolsFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings, TTSSettings
from pipecat.services.tts_service import TTSService


@dataclass(frozen=True)
class CallTool:
    """A scripted LLM step that calls a tool instead of speaking."""

    name: str
    arguments: dict = field(default_factory=dict)


class ScriptedLLM(LLMService):
    """Answers each LLM run with the next scripted step: a line to speak or a CallTool."""

    def __init__(self, steps: list[str | CallTool]):
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

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return
        step = self.steps.pop(0) if self.steps else "(script exhausted)"
        await self.push_frame(LLMFullResponseStartFrame())
        if isinstance(step, CallTool):
            await self.run_function_calls(
                [
                    FunctionCallFromLLM(
                        function_name=step.name,
                        tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
                        arguments=step.arguments,
                        context=frame.context,
                    )
                ]
            )
        else:
            await self.push_frame(LLMTextFrame(step))
        await self.push_frame(LLMFullResponseEndFrame())


class SilentSTT(FrameProcessor):
    """Hears nothing: passes every frame through and never transcribes."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class RunLLMOnceGreeted(FrameProcessor):
    """Stands in for STT and a Caller who says nothing aloud. The LLM gets one turn once the flow has set its tools."""

    def __init__(self):
        super().__init__()
        self._run = True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if self._run and isinstance(frame, LLMSetToolsFrame):
            self._run = False
            await self.push_frame(LLMRunFrame())


class RecordingTTS(TTSService):
    """Records the text it is asked to speak and answers with a short burst of silence."""

    def __init__(self):
        super().__init__(
            push_start_frame=True,
            push_stop_frames=True,
            settings=TTSSettings(model=None, voice=None, language=None),
        )
        self.spoken: list[str] = []

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        self.spoken.append(text)
        tenth_of_a_second = b"\x00\x00" * (self.sample_rate // 10)
        yield TTSAudioRawFrame(
            audio=tenth_of_a_second, sample_rate=self.sample_rate, num_channels=1, context_id=context_id
        )
