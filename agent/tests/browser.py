"""Plays the browser's side of a browser call, the way Pipecat's prebuilt page does, with aiortc in place of Chrome.

The page asks the server for a session with `POST /start`, then sends a WebRTC offer with the microphone
track to `/sessions/<id>/api/offer`. The microphone here is silent: tests stand a scripted Caller in for STT.
"""

import asyncio
import json
import time
import uuid

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack, MediaStreamTrack


class Browser:
    def __init__(self, app):
        self.session_id: str | None = None
        self._app = app
        self._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8765")
        # No STUN server: aiortc would ask Google's by default, and both ends are on this machine.
        self._peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self._speaker: asyncio.Future[MediaStreamTrack] = asyncio.get_running_loop().create_future()
        self._keepalive: asyncio.Task | None = None

        @self._peer.on("track")
        def on_track(track: MediaStreamTrack):
            if track.kind == "audio" and not self._speaker.done():
                self._speaker.set_result(track)

    async def connect(self) -> None:
        # What the prebuilt page sends to start a SmallWebRTC session.
        started = await self._http.post(
            "/start", json={"createDailyRoom": False, "enableDefaultIceServers": True, "transport": "webrtc"}
        )
        started.raise_for_status()
        self.session_id = started.json()["sessionId"]
        self._peer.addTrack(AudioStreamTrack())
        # The page keeps a data channel open and pings over it, so the server can tell it is still there.
        self._channel = channel = self._peer.createDataChannel("chat", ordered=True)
        self._channel_open = asyncio.Event()

        @channel.on("open")
        def on_open():
            self._channel_open.set()
            self._keepalive = asyncio.create_task(self._ping(channel))

        await self._peer.setLocalDescription(await self._peer.createOffer())
        answered = await self._http.post(
            f"/sessions/{self.session_id}/api/offer",
            json={"sdp": self._peer.localDescription.sdp, "type": "offer", "pc_id": None, "restart_pc": False},
        )
        answered.raise_for_status()
        answer = answered.json()
        await self._peer.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

    async def type(self, line: str) -> None:
        """Sends a line from the page's message box, which reaches the agent as text instead of speech."""
        message = {
            "id": uuid.uuid4().hex,
            "label": "rtvi-ai",
            "type": "send-text",
            "data": {"content": line, "options": {"run_immediately": True, "audio_response": True}},
        }
        await asyncio.wait_for(self._channel_open.wait(), 10)
        self._channel.send(json.dumps(message))

    async def hear(self, seconds: float = 10) -> None:
        """Waits for the first audio the agent sends."""
        speaker = await asyncio.wait_for(self._speaker, seconds)
        await asyncio.wait_for(speaker.recv(), seconds)

    def go_quiet(self) -> None:
        """Stops pinging, as a page does when the laptop sleeps or the network drops, without hanging up."""
        if self._keepalive:
            self._keepalive.cancel()

    async def hang_up(self, seconds: float = 10) -> None:
        """Closes the call the way the page's disconnect button does, then waits for the server to finish it.

        The goodbye is one UDP datagram and can go missing; then the server ends the call once the pings stop.
        """
        self.go_quiet()
        # The close runs on its own while the server ends the call. Its last step can hang on Windows: aioice
        # waits forever for a UDP socket it closed to report closed. So the close is dropped only once the
        # server has ended the call.
        closing = asyncio.create_task(self._peer.close())
        await self._http.aclose()
        try:
            await self.call_ended(seconds)
        finally:
            await asyncio.wait({closing}, timeout=2)
            closing.cancel()

    async def call_ended(self, seconds: float = 10) -> None:
        """Waits for the server to finish the call, as it does after the agent says goodbye."""
        async with asyncio.timeout(seconds):
            while self._app.state.calls_in_progress:
                await asyncio.sleep(0.05)

    @staticmethod
    async def _ping(channel) -> None:
        while True:
            channel.send(f"ping: {time.time()}")
            await asyncio.sleep(1)
