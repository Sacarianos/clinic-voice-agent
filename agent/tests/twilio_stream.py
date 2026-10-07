"""Plays Twilio's side of a Media Streams WebSocket."""

CALL_SID = "CA00000000000000000000000000000001"
STREAM_SID = "MZ00000000000000000000000000000001"


def start_media_stream(twilio):
    """The two messages Twilio sends when a <Stream> opens."""
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
                "customParameters": {},
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            },
        }
    )


def next_media_message(twilio):
    while True:
        message = twilio.receive_json()
        if message["event"] == "media":
            return message


def hang_up(twilio):
    """Twilio sends a stop message and then closes the socket."""
    twilio.send_json({"event": "stop", "streamSid": STREAM_SID, "stop": {"callSid": CALL_SID}})
    twilio.close()
