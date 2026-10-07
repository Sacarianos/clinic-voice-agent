"""The text transport: one call driven by typed Caller lines, with no STT or TTS.

It runs the phone's pipeline and conversation flow with the audio ends swapped out. Caller lines go
in as finished transcripts; what the agent says, the tools it called and the tools it was offered come
out. The LLM is whatever it is given: a real configured model or a scripted fake.
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from pipecat.frames.frames import (
    ControlFrame,
    EndFrame,
    Frame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    LLMAssistantPushAggregationFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    SystemFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.worker import PipelineParams
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import LLMService
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from clinic_agent.conversation import start_conversation
from clinic_agent.ehr import EhrAdapter
from clinic_agent.pipeline import build_call


class CallEnded(Exception):
    pass


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict
    result: Any


class TextCall:
    """Use as `async with TextCall(llm, ehr) as call:`. Entering waits for the agent's greeting."""

    def __init__(self, llm: LLMService, ehr: EhrAdapter, *, reply_timeout_secs: float = 10):
        self.transcript: list[tuple[str, str]] = []  # ("caller" | "agent", line), in order
        self.tool_calls: list[ToolCall] = []
        self.offered_tools: list[list[str]] = []  # tool names the LLM was offered, one list per LLM run
        self._ehr = ehr
        self._reply_timeout_secs = reply_timeout_secs
        self._speech = _SpokenText(self.transcript)
        self._watch = _TurnWatch(llm, self)
        self._call = build_call(
            llm=llm,
            hear=[],
            speak=[self._speech],
            user_params=LLMUserAggregatorParams(user_turn_strategies=ExternalUserTurnStrategies()),
            params=PipelineParams(),
            idle_timeout_secs=None,
            enable_rtvi=False,
            observers=[self._watch],
        )
        self._watch.last_processor = self._call.aggregators.assistant()
        self._flow = None
        self._run: asyncio.Task | None = None

    @property
    def agent_lines(self) -> list[str]:
        return [line for speaker, line in self.transcript if speaker == "agent"]

    @property
    def state(self) -> str | None:
        """The conversation node the call is in."""
        return self._flow.current_node if self._flow else None

    @property
    def ended(self) -> bool:
        return self._run is not None and self._run.done()

    def tool_results(self, name: str) -> list[Any]:
        return [call.result for call in self.tool_calls if call.name == name]

    def __repr__(self) -> str:
        lines = [f"{speaker}: {line}" for speaker, line in self.transcript]
        tools = [f"{call.name}({call.arguments}) -> {call.result}" for call in self.tool_calls]
        return "\n".join(["TextCall", *lines, f"state: {self.state}", *tools])

    async def __aenter__(self) -> "TextCall":
        started = asyncio.Event()

        @self._call.worker.event_handler("on_pipeline_started")
        async def on_pipeline_started(worker, frame):
            started.set()

        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(self._call.worker)
        self._run = asyncio.create_task(runner.run())
        await self._until(started.wait())
        self._flow = await self._until(start_conversation(self._call, self._ehr))
        await self._agent_turn()
        return self

    async def __aexit__(self, *exc_info) -> None:
        if not self.ended:
            await self._call.worker.queue_frame(EndFrame())
        await asyncio.wait_for(self._run, self._reply_timeout_secs)

    async def say(self, line: str) -> str:
        """Speak a Caller line and wait for the agent to finish its turn. Returns what the agent said."""
        if self.ended:
            raise CallEnded(f"the call ended before the Caller could say {line!r}")
        heard = len(self.agent_lines)
        self.transcript.append(("caller", line))
        llm_runs = self._watch.llm_runs
        await self._call.worker.queue_frames(
            [
                ProposedUserStartedSpeakingFrame(),
                TranscriptionFrame(text=line, user_id="caller", timestamp="", finalized=True),
                ProposedUserStoppedSpeakingFrame(),
            ]
        )
        await self._until(self._watch.llm_ran_since(llm_runs))
        await self._agent_turn()
        return " ".join(self.agent_lines[heard:])

    async def converse(self, lines: list[str]) -> None:
        """Speaks each Caller line in turn, waiting for the agent after each."""
        for line in lines:
            await self.say(line)

    async def _agent_turn(self) -> None:
        """Waits until the agent is waiting for the Caller, or the call has ended.

        The agent's turn can end in an LLM reply, in a line the flow speaks itself, or in a hang-up,
        and tools and node changes run in between. So instead of looking for a last frame, this sends a
        marker frame through the pipeline until two round trips in a row see nothing else happen.
        """
        quiet_round_trips = 0
        while quiet_round_trips < 2 and not self.ended:
            activity = self._watch.activity
            arrived = self._watch.expect_marker()
            await self._call.worker.queue_frame(_Marker(marker_id=arrived.id))
            await self._until(arrived.wait())
            quiet = not self._watch.busy and self._watch.activity == activity
            quiet_round_trips = quiet_round_trips + 1 if quiet else 0

    async def _until(self, awaitable):
        """Awaits it, unless the call ends first. Fails when the agent takes longer than the reply timeout."""
        task = asyncio.ensure_future(awaitable)
        done, _ = await asyncio.wait(
            {task, self._run}, timeout=self._reply_timeout_secs, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            return task.result()
        task.cancel()
        if self._run in done:
            self._run.result()  # surfaces a pipeline crash
            return None
        raise TimeoutError(f"the agent did not finish its turn within {self._reply_timeout_secs}s: {self.transcript}")


@dataclass
class _Marker(ControlFrame):
    marker_id: str = ""

    def __post_init__(self):
        super().__post_init__()
        self.interruptible = False


class _MarkerArrival(asyncio.Event):
    def __init__(self):
        super().__init__()
        self.id = uuid.uuid4().hex


class _SpokenText(FrameProcessor):
    """Sits where TTS and the phone's audio output would be. Records each line the Caller would hear.

    Lines the flow speaks itself (TTSSpeakFrame) reach the LLM context through TTS on the phone. Here
    this pushes the frames a TTS would, so the context holds the same conversation in both.
    """

    def __init__(self, transcript: list[tuple[str, str]]):
        super().__init__()
        self._transcript = transcript
        self._reply = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._reply = ""
        elif isinstance(frame, LLMTextFrame):
            self._reply += frame.text
        elif isinstance(frame, LLMFullResponseEndFrame) and self._reply.strip():
            self._transcript.append(("agent", self._reply.strip()))
            self._reply = ""
        elif isinstance(frame, TTSSpeakFrame):
            self._transcript.append(("agent", frame.text))
            await self._speak(frame)
            return
        await self.push_frame(frame, direction)

    async def _speak(self, frame: TTSSpeakFrame):
        context_id = uuid.uuid4().hex
        await self.push_frame(TTSStartedFrame(context_id=context_id, append_to_context=frame.append_to_context))
        text = TTSTextFrame(frame.text, aggregated_by="sentence", context_id=context_id)
        text.append_to_context = frame.append_to_context
        text.includes_inter_frame_spaces = True
        await self.push_frame(text)
        await self.push_frame(TTSStoppedFrame(context_id=context_id))
        await self.push_frame(LLMAssistantPushAggregationFrame())


class _TurnWatch(BaseObserver):
    """Watches every frame pushed in the pipeline, in order, to tell when the agent's turn is over."""

    def __init__(self, llm: LLMService, call: TextCall):
        super().__init__()
        self.last_processor: FrameProcessor | None = None
        self.activity = 0  # frames seen, other than markers
        self.llm_runs = 0
        self._llm = llm
        self._call = call
        self._llm_running = False
        self._tools_running: set[str] = set()
        self._markers: dict[str, _MarkerArrival] = {}
        self._llm_ran = asyncio.Condition()

    @property
    def busy(self) -> bool:
        return self._llm_running or bool(self._tools_running)

    def expect_marker(self) -> _MarkerArrival:
        arrival = _MarkerArrival()
        self._markers[arrival.id] = arrival
        return arrival

    async def llm_ran_since(self, runs: int) -> None:
        async with self._llm_ran:
            await self._llm_ran.wait_for(lambda: self.llm_runs > runs)

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if isinstance(frame, _Marker):
            if data.source is self.last_processor and frame.marker_id in self._markers:
                self._markers.pop(frame.marker_id).set()
            return
        if not isinstance(frame, SystemFrame):
            self.activity += 1
        from_llm = data.source is self._llm and data.direction == FrameDirection.DOWNSTREAM
        if isinstance(frame, LLMContextFrame) and data.destination is self._llm:
            self._llm_running = True
            tools = frame.context.tools
            self._call.offered_tools.append([tool.name for tool in getattr(tools, "standard_tools", [])])
            async with self._llm_ran:
                self.llm_runs += 1
                self._llm_ran.notify_all()
        elif isinstance(frame, LLMFullResponseEndFrame) and from_llm:
            self._llm_running = False
        elif isinstance(frame, FunctionCallsStartedFrame) and from_llm:
            self._tools_running.update(call.tool_call_id for call in frame.function_calls)
        elif isinstance(frame, FunctionCallResultFrame) and from_llm:
            self._tools_running.discard(frame.tool_call_id)
            self._call.tool_calls.append(ToolCall(frame.function_name, dict(frame.arguments), frame.result))
