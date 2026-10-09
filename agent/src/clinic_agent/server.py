"""Voice server: browser calls over WebRTC, and phone calls through Twilio when it is set up.

A browser call opens Pipecat's prebuilt page at `/`, which talks to the server over WebRTC. A phone call
comes in through the Twilio webhook and its media stream WebSocket. Both run the same pipeline and the
same conversation; only the transport at each end differs.
"""

import asyncio
import base64
import hashlib
import hmac
import os
import sys
import uuid
from collections.abc import Callable, Coroutine, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import quoteattr

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import RedirectResponse, Response
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.worker import PipelineParams
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner
from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI

from clinic_agent.config import ConfigError, require
from clinic_agent.conversation import start_conversation
from clinic_agent.logs import configure_logging
from clinic_agent.pipeline import build_call
from clinic_agent.services import VoiceServices, phone_services
from clinic_agent.tracing import configure_tracing

# Twilio Media Streams carry 8 kHz mu-law. The serializer converts to and from PCM at this rate.
PHONE_SAMPLE_RATE = 8000
# The WebRTC transport resamples the browser's 48 kHz Opus. Silero VAD takes 16 kHz; Aura-2 speaks 24 kHz.
BROWSER_IN_SAMPLE_RATE = 16000
BROWSER_OUT_SAMPLE_RATE = 24000
DEFAULT_PORT = 8765
# The page pings every second. A browser call ends this long after the last ping, hung up or not.
BROWSER_GONE_AFTER_SECS = 5
# How long Pipecat's SmallWebRTCConnection.is_connected() stays true after the last ping.
_PING_FRESH_SECS = 3


@dataclass(frozen=True)
class TwilioAccount:
    """Checks that webhooks come from Twilio, and lets the agent hang up through Twilio's REST API."""

    account_sid: str
    auth_token: str


def create_app(
    make_services: Callable[[], VoiceServices],
    *,
    twilio: TwilioAccount | None = None,
    hang_up_through_twilio: bool = True,
    tracing: bool = False,
) -> FastAPI:
    """Browser calls are always on. Phone calls come in only with a Twilio account to check their webhooks and hang them up."""
    webrtc = SmallWebRTCRequestHandler()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await webrtc.close()

    app = FastAPI(lifespan=lifespan)
    # Tests wait for this to drop back to 0 after hanging up, so they don't tear a call down mid-cleanup.
    app.state.calls_in_progress = 0

    async def run_call(
        transport: BaseTransport,
        params: PipelineParams,
        conversation_id: str,
        caller_phone: str | None,
        gone: Coroutine[Any, Any, None] | None = None,
    ):
        app.state.calls_in_progress += 1
        try:
            await _run_call(make_services(), transport, params, conversation_id, caller_phone, gone, tracing=tracing)
        finally:
            app.state.calls_in_progress -= 1

    _add_browser_routes(app, webrtc, run_call)
    if twilio:
        _add_phone_routes(app, twilio, run_call, hang_up_through_twilio=hang_up_through_twilio)
    return app


def _add_browser_routes(app: FastAPI, webrtc: SmallWebRTCRequestHandler, run_call) -> None:
    """The routes Pipecat's prebuilt page uses: the page itself, a session to start, and the WebRTC offer."""
    app.mount("/client", PipecatPrebuiltUI)
    calls: set[asyncio.Task] = set()

    @app.get("/", include_in_schema=False)
    async def page() -> RedirectResponse:
        return RedirectResponse(url="/client/")

    @app.post("/start")
    async def start() -> dict:
        # No ICE servers: the browser and the server are on the same machine, so host candidates connect them.
        return {"sessionId": str(uuid.uuid4())}

    @app.post("/sessions/{session_id}/api/offer")
    async def offer(session_id: uuid.UUID, request: SmallWebRTCRequest) -> dict:
        async def call_in_background(connection: SmallWebRTCConnection) -> None:
            transport = SmallWebRTCTransport(
                webrtc_connection=connection,
                params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
            )
            params = PipelineParams(
                audio_in_sample_rate=BROWSER_IN_SAMPLE_RATE,
                audio_out_sample_rate=BROWSER_OUT_SAMPLE_RATE,
                enable_metrics=True,
                enable_usage_metrics=True,
            )
            # A browser has no caller ID, so a Callback Request asks the Caller for a number.
            task = asyncio.create_task(run_call(transport, params, str(session_id), None, _gone(connection)))
            calls.add(task)
            task.add_done_callback(calls.discard)

        return await webrtc.handle_web_request(request, call_in_background)

    @app.patch("/sessions/{session_id}/api/offer")
    async def ice_candidates(session_id: uuid.UUID, request: SmallWebRTCPatchRequest) -> dict:
        await webrtc.handle_patch_request(request)
        return {"status": "success"}


def _add_phone_routes(app: FastAPI, twilio: TwilioAccount, run_call, *, hang_up_through_twilio: bool) -> None:
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
        params = PipelineParams(
            audio_in_sample_rate=PHONE_SAMPLE_RATE,
            audio_out_sample_rate=PHONE_SAMPLE_RATE,
            enable_metrics=True,  # STT, LLM and TTS time to first byte on each span
            enable_usage_metrics=True,
        )
        await run_call(transport, params, call_data.call_id, call_data.from_number or None)


async def _run_call(
    services: VoiceServices,
    transport: BaseTransport,
    params: PipelineParams,
    conversation_id: str,
    caller_phone: str | None,
    gone: Coroutine[Any, Any, None] | None,
    *,
    tracing: bool,
) -> None:
    """One call, from the Caller connecting to hanging up. Traced as one conversation keyed by conversation_id.

    gone, when given, returns once the Caller has left without the transport noticing. The call then ends.
    """
    call = build_call(
        llm=services.llm,
        hear=[transport.input(), services.stt],
        speak=[services.tts, transport.output()],
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
        params=params,
        enable_tracing=tracing,
        enable_turn_tracking=True,
        conversation_id=conversation_id,
        additional_span_attributes={"langfuse.session.id": conversation_id},
        observers=[_latency_log()],
    )

    starting: list[asyncio.Task] = []

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        starting.append(asyncio.current_task())
        await start_conversation(call, services.ehr, caller_phone)

    async def caller_left():
        # Starting the conversation waits until the greeting has been spoken. If the Caller hangs up
        # first, it never is, and the transport would wait for that start forever before shutting down.
        for task in starting:
            task.cancel()
        await call.end_now()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await caller_left()

    async def watch_for_a_silent_exit():
        await gone
        logger.info("The Caller stopped answering without hanging up. Ending the call.")
        await caller_left()

    watching = asyncio.create_task(watch_for_a_silent_exit()) if gone else None
    runner = WorkerRunner(handle_sigint=False)  # uvicorn owns the signals
    await runner.add_workers(call.worker)
    try:
        await runner.run()
    finally:
        if watching:
            watching.cancel()


async def _gone(connection: SmallWebRTCConnection) -> None:
    """Returns once the browser has sent no ping for BROWSER_GONE_AFTER_SECS.

    A page's hang-up reaches the server as one UDP datagram, which can go missing, and a laptop that sleeps
    or drops off the network sends none. Without this the call would run on until ICE gives up, about 30 s on.
    """
    while not connection.is_connected():
        await asyncio.sleep(0.5)
    loop = asyncio.get_running_loop()
    stale_since = None
    while True:
        await asyncio.sleep(0.5)
        if connection.is_connected():
            stale_since = None
        elif stale_since is None:
            stale_since = loop.time()
        elif loop.time() - stale_since >= BROWSER_GONE_AFTER_SECS - _PING_FRESH_SECS:
            return


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
    """Raises ConfigError when a key the server needs is missing. Phone routes are on only with Twilio credentials."""
    make_services = phone_services(env)
    return create_app(make_services, twilio=_twilio_account(env), tracing=configure_tracing(env))


def _twilio_account(env: Mapping[str, str]) -> TwilioAccount | None:
    """None without Twilio credentials. Half of them is a mistake worth stopping for."""
    if not env.get("TWILIO_ACCOUNT_SID") and not env.get("TWILIO_AUTH_TOKEN"):
        return None
    return TwilioAccount(
        account_sid=require(env, "TWILIO_ACCOUNT_SID", "Hanging up calls through Twilio"),
        auth_token=require(env, "TWILIO_AUTH_TOKEN", "Checking that webhooks come from Twilio"),
    )


def main() -> None:
    env = os.environ
    configure_logging(env.get("LOG_LEVEL", "INFO"))
    try:
        app = app_from_env(env)
    except ConfigError as error:
        sys.exit(f"clinic-voice-server: {error}")
    port = int(env.get("PORT", DEFAULT_PORT))
    logger.info(f"Open http://localhost:{port} in a browser to talk to the agent")
    # Without uvicorn's own log config its loggers reach the masked setup above, like everything else.
    uvicorn.run(app, host="127.0.0.1", port=port, log_config=None)
