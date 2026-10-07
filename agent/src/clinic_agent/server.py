"""Voice server: the Twilio webhook and the media stream WebSocket."""

import base64
import hashlib
import hmac
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from xml.sax.saxutils import quoteattr

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.worker import PipelineParams
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from clinic_agent.config import ConfigError, require
from clinic_agent.conversation import start_conversation
from clinic_agent.pipeline import build_call
from clinic_agent.services import VoiceServices, phone_services
from clinic_agent.tracing import configure_tracing

# Twilio Media Streams carry 8 kHz mu-law. The serializer converts to and from PCM at this rate.
PHONE_SAMPLE_RATE = 8000
DEFAULT_PORT = 8765
UNKNOWN_CALLER_PHONE = "unknown"


@dataclass(frozen=True)
class TwilioAccount:
    """Checks that webhooks come from Twilio, and lets the agent hang up through Twilio's REST API."""

    account_sid: str
    auth_token: str


def create_app(
    make_services: Callable[[], VoiceServices],
    *,
    twilio: TwilioAccount,
    hang_up_through_twilio: bool = True,
    tracing: bool = False,
) -> FastAPI:
    app = FastAPI()

    @app.post("/voice")
    async def voice(request: Request) -> Response:
        form = await request.form()
        # Twilio signs the https URL set in its console. ngrok ends TLS and forwards plain HTTP, but keeps
        # that public host in the Host header, so the URL rebuilt from it is the one Twilio signed.
        public_url = str(request.url.replace(scheme="https"))
        signature = request.headers.get("x-twilio-signature")
        if not _signed_by_twilio(twilio.auth_token, public_url, form.multi_items(), signature):
            logger.warning(f"Refused a webhook without a valid Twilio signature for {public_url}")
            return Response(status_code=403)
        # Twilio only streams to wss://.
        stream_url = f"wss://{request.headers['host']}/ws"
        # The Caller's number rides along as a stream parameter. Callback Requests call it back.
        caller = form.get("From")
        parameter = f"<Parameter name={quoteattr('from_number')} value={quoteattr(str(caller))}/>" if caller else ""
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Connect><Stream url={quoteattr(stream_url)}>{parameter}</Stream></Connect></Response>"
        )
        return Response(content=twiml, media_type="application/xml")

    @app.websocket("/ws")
    async def media_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        _, call_data = await parse_telephony_websocket(websocket)
        serializer = TwilioFrameSerializer(
            stream_sid=call_data.stream_id,
            call_sid=call_data.call_id,
            account_sid=twilio.account_sid,
            auth_token=twilio.auth_token,
            params=TwilioFrameSerializer.InputParams(auto_hang_up=hang_up_through_twilio),
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
            observers=[_latency_log()],
        )

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            await start_conversation(call, services.ehr, call_data.from_number or UNKNOWN_CALLER_PHONE)

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            await call.worker.cancel()

        runner = WorkerRunner(handle_sigint=False)  # uvicorn owns the signals
        await runner.add_workers(call.worker)
        await runner.run()

    return app


def _latency_log() -> UserBotLatencyObserver:
    """Logs where each response's time went, from the Caller going quiet to the agent's first audio."""
    observer = UserBotLatencyObserver()

    @observer.event_handler("on_first_bot_speech_latency")
    async def on_first_bot_speech_latency(observer, seconds):
        logger.info(f"Greeting started {seconds:.3f}s after the call connected")

    @observer.event_handler("on_latency_breakdown")
    async def on_latency_breakdown(observer, breakdown):
        if lines := breakdown.turn_contribution_lines():
            logger.info("Response latency:\n" + "\n".join(lines))

    return observer


def _signed_by_twilio(auth_token: str, url: str, form: Iterable[tuple[str, str]], signature: str | None) -> bool:
    """Twilio signs a webhook with base64 HMAC-SHA1, keyed by the auth token, of the URL it called followed
    by every POST parameter's name and value, sorted by name.
    """
    if not signature:
        return False
    signed = url + "".join(name + value for name, value in sorted(form))
    expected = hmac.new(auth_token.encode(), signed.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(expected).decode(), signature)


def app_from_env(env: Mapping[str, str]) -> FastAPI:
    """Raises ConfigError when a key the server needs is missing."""
    make_services = phone_services(env)
    twilio = TwilioAccount(
        account_sid=require(env, "TWILIO_ACCOUNT_SID", "Hanging up calls through Twilio"),
        auth_token=require(env, "TWILIO_AUTH_TOKEN", "Checking that webhooks come from Twilio"),
    )
    return create_app(make_services, twilio=twilio, tracing=configure_tracing(env))


def main() -> None:
    env = os.environ
    logger.remove()
    logger.add(sys.stderr, level=env.get("LOG_LEVEL", "INFO"))
    try:
        app = app_from_env(env)
    except ConfigError as error:
        sys.exit(f"clinic-voice-server: {error}")
    uvicorn.run(app, host="127.0.0.1", port=int(env.get("PORT", DEFAULT_PORT)))
