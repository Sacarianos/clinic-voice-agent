"""Plays Twilio's side of a Media Streams WebSocket."""

import time

CALL_SID = "CA00000000000000000000000000000001"
STREAM_SID = "MZ00000000000000000000000000000001"


def start_media_stream(twilio, from_number=None):
    """The two messages Twilio sends when a <Stream> opens. It passes along the <Parameter>s of the TwiML."""
    twilio.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
    twilio.send_json(
        {
            "event": "start",
            "sequenceNumber": "1",
            "streamSid": STREAM_SID,
            "start": {
                "streamSid": STREAM_SID,
                "callSid": CALL_SID,
                "accountSid": "AC00000000000000000000000000000001",
                "tracks": ["inbound"],
                "customParameters": {"from_number": from_number} if from_number else {},
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            },
        }
    )


def next_media_message(twilio):
    while True:
        message = twilio.receive_json()
        if message["event"] == "media":
            return message


def hang_up(twilio, app, seconds=10):
    """Twilio sends a stop message and then closes the socket.

    Then this waits for the server to finish the call. Leaving the TestClient's websocket block cancels
    the server's handler, and a cancel that lands while the pipeline is still shutting down fails the test.
    """
    twilio.send_json({"event": "stop", "streamSid": STREAM_SID, "stop": {"callSid": CALL_SID}})
    twilio.close()
    deadline = time.monotonic() + seconds
    while app.state.calls_in_progress:
        if time.monotonic() > deadline:
            raise AssertionError(f"the server was still on the call {seconds}s after the Caller hung up")
        time.sleep(0.05)

