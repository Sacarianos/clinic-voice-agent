"""Voice server: the Twilio webhook and the media stream WebSocket."""

from collections.abc import Callable
from xml.sax.saxutils import quoteattr

from fastapi import FastAPI, Request
from fastapi.responses import Response


def create_app(make_services: Callable) -> FastAPI:
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

    return app
