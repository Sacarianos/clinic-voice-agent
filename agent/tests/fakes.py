"""Stand-ins for Deepgram and the LLM so a call runs without network or API keys."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallFromLLM,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    LLMSetToolsFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, FrameProcessorSetup
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings, TTSSettings
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601
from pipecat.utils.tracing.service_decorators import traced_llm, traced_stt, traced_tts


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
        await self.push_frame(LLMFullResponseEndFrame())


class SilentSTT(FrameProcessor):
    """Hears nothing: passes every frame through and never transcribes."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


@dataclass(frozen=True)
class TalkOver:
    """A Caller line said while the agent is still talking."""

    line: str


class ScriptedCaller(FrameProcessor):
    """Stands in for STT and a Caller on the phone. Each line answers the agent's next stretch of speech.

    A plain line comes a moment after the agent stops talking. A TalkOver line comes a moment after it
    starts, and cuts it off. Each line arrives as a finished transcript, the way Deepgram delivers one,
    and with tracing on it is traced as an `stt` span holding the transcript, as Deepgram's are.
    """

    def __init__(self, lines: list[str | TalkOver], pause_secs: float = 0.3):
        super().__init__()
        self.lines = list(lines)
        self._pause_secs = pause_secs
        self._tracing_enabled = False

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        # Pipecat's STT tracing reads these, as it does on a real STT service.
        self._tracing_enabled = setup.enable_tracing
        self._tracing_context = setup.tracing_context

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if direction != FrameDirection.UPSTREAM or not self.lines:
            return
        talks_over = isinstance(self.lines[0], TalkOver)
        if (isinstance(frame, BotStartedSpeakingFrame) and talks_over) or (
            isinstance(frame, BotStoppedSpeakingFrame) and not talks_over
        ):
            line = self.lines.pop(0)
            self.create_task(self._say(line.line if talks_over else line))

    @traced_stt
    async def _say(self, line: str):
        await asyncio.sleep(self._pause_secs)
        await self.push_frame(
            TranscriptionFrame(text=line, user_id="caller", timestamp=time_now_iso8601(), finalized=True)
        )


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
    """Records the text it is asked to speak and answers with silence, a tenth of a second per sentence by default.

    Like Deepgram's TTS, it traces each sentence as a `tts` span holding the text.
    """

    def __init__(self, seconds_per_sentence: float = 0.1):
        super().__init__(
            push_start_frame=True,
            push_stop_frames=True,
            settings=TTSSettings(model=None, voice=None, language=None),
        )
        self.spoken: list[str] = []
        self._seconds_per_sentence = seconds_per_sentence

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        self.spoken.append(text)
        silence = b"\x00\x00" * int(self.sample_rate * self._seconds_per_sentence)
        yield TTSAudioRawFrame(audio=silence, sample_rate=self.sample_rate, num_channels=1, context_id=context_id)
