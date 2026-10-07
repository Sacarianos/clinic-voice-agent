"""Voice server: the Twilio webhook and the media stream WebSocket."""

from collections.abc import Callable
from dataclasses import dataclass
from xml.sax.saxutils import quoteattr

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.worker import PipelineParams
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.llm_service import LLMService
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from clinic_agent.pipeline import build_call
from clinic_agent.receptionist import start_conversation

# Twilio Media Streams carry 8 kHz mu-law. The serializer converts to and from PCM at this rate.
PHONE_SAMPLE_RATE = 8000


@dataclass(frozen=True)
class VoiceServices:
    """The parts of one call that talk to outside services."""

    stt: FrameProcessor
    llm: LLMService
    tts: FrameProcessor


@dataclass(frozen=True)
class TwilioAccount:
    """Lets the agent hang up through Twilio's REST API when it ends a call."""

    account_sid: str
    auth_token: str


def create_app(
    make_services: Callable[[], VoiceServices],
    *,
    twilio: TwilioAccount | None = None,
    tracing: bool = False,
) -> FastAPI:
    app = FastAPI()

    @app.post("/voice")
    async def voice(request: Request) -> Response:
        # Twilio only streams to wss://, and ngrok keeps the public host in the Host header.
        stream_url = f"wss://{request.headers['host']}/ws"
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Connect><Stream url={quoteattr(stream_url)}/></Connect></Response>"
        )
        return Response(content=twiml, media_type="application/xml")

    @app.websocket("/ws")
    async def media_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        _, call_data = await parse_telephony_websocket(websocket)
        serializer = TwilioFrameSerializer(
            stream_sid=call_data.stream_id,
            call_sid=call_data.call_id,
            account_sid=twilio.account_sid if twilio else "",
            auth_token=twilio.auth_token if twilio else "",
            params=TwilioFrameSerializer.InputParams(auto_hang_up=twilio is not None),
        )
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=serializer,
            ),
        )
        services = make_services()
        call = build_call(
            llm=services.llm,
            hear=[transport.input(), services.stt],
            speak=[services.tts, transport.output()],
            user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
            params=PipelineParams(
                audio_in_sample_rate=PHONE_SAMPLE_RATE,
                audio_out_sample_rate=PHONE_SAMPLE_RATE,
                enable_metrics=True,  # STT, LLM and TTS time to first byte on each span
                enable_usage_metrics=True,
            ),
            enable_tracing=tracing,
            enable_turn_tracking=True,
            conversation_id=call_data.call_id,
            additional_span_attributes={"langfuse.session.id": call_data.call_id},
        )

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            await start_conversation(call)

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            await call.worker.cancel()

        runner = WorkerRunner(handle_sigint=False)  # uvicorn owns the signals
        await runner.add_workers(call.worker)
        await runner.run()

    return app
