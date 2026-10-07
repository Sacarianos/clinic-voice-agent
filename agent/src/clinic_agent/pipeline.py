"""One call's pipeline, independent of how the Caller is heard and answered.

The phone puts the Twilio transport and STT in front of the conversation and TTS
behind it. A text transport puts typed lines in and captures replies instead. The
LLM is whatever the named config built. Everything between is shared.
"""

from dataclasses import dataclass

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.llm_service import LLMService


@dataclass(frozen=True)
class Call:
    worker: PipelineWorker
    llm: LLMService
    context: LLMContext
    aggregators: LLMContextAggregatorPair


def build_call(
    *,
    llm: LLMService,
    hear: list[FrameProcessor],
    speak: list[FrameProcessor],
    user_params: LLMUserAggregatorParams,
    params: PipelineParams,
    **worker_options,
) -> Call:
    """hear: processors that turn the Caller into transcripts. speak: processors that turn replies into output."""
    context = LLMContext()
    aggregators = LLMContextAggregatorPair(context, user_params=user_params)
    pipeline = Pipeline([*hear, aggregators.user(), llm, *speak, aggregators.assistant()])
    worker = PipelineWorker(pipeline, params=params, **worker_options)
    return Call(worker=worker, llm=llm, context=context, aggregators=aggregators)
