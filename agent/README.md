# Voice server

Part of the [Clinic Voice Agent](../README.md). This is the reference for the voice server and the conversation it runs.


The voice server lives in `agent/`. You can talk to the agent in two ways: from a browser on your own computer, through its microphone, or over a real phone call through Twilio. Every call runs its own Pipecat pipeline: Silero VAD, Deepgram Nova-3, the configured LLM, and Deepgram Aura-2. Nova-3 gets the three Providers' full names and surnames as keyterms, so it hears names like Szczepanski and Kowalczyk. Browser calls, phone calls and the text transport build the same pipeline in `pipeline.py` and run the same conversation flow; only the ends that hear and speak differ. A Caller can talk over anything the agent says, including the greeting and the Read-back.

The conversation is a `pipecat.flows` state machine in `agent/src/clinic_agent/conversation.py`. The agent greets the Caller and runs Identity Verification through the adapter before anything else. `verify_patient` makes the LLM say whether the details are the Caller's own. When it says they belong to someone else, the details never reach the EHR and the call ends with a Proxy Caller Handoff. A name that matches several Patients gets a request to spell the last name. The flow remembers that it asked, so only the next attempt is sent as spelled. If the spelled name still matches more than one Patient, the call ends with a Handoff instead of a guess. A failed attempt gets the same failure message whatever didn't match, and a second one ends the call with a Handoff message. Once verified, the call moves on to Intent. Each state offers the LLM only its own tools, so nothing past verification can be reached before it. [ADR 0003](../docs/adr/0003-safety-rules-live-in-the-state-machine.md) says why.

Booking lives in `agent/src/clinic_agent/booking.py`. From Intent the agent searches for Slots by Provider, days and part of day, and offers two or three. When the Caller picks one and says what the visit is for, `choose_slot` moves to the Read-back, where the agent itself says the Provider, date, time and Visit Type. The Read-back offers one tool, `record_read_back_answer`, which records the Caller's answer as yes, no or change. All three writes share it from `read_back.py`. Only a recorded yes moves on, to a state whose only tool is `book_appointment`. It takes no arguments: it books exactly what was read back, under an idempotency key made for that Read-back. A no or a change goes back to choosing a time without starting over. The agent says the appointment is booked only when the adapter answers `succeeded`. A Slot taken in the meantime gets an apology and new offers.

Rescheduling and cancelling live in `agent/src/clinic_agent/appointments.py`. From Intent, `list_appointments` tells the Caller their upcoming appointments. With more than one, the agent asks which. Choosing one to cancel goes to a Read-back of that appointment. Choosing one to reschedule goes to the same Slot search as booking, and picking a new time goes to a Read-back of the old and new times. As with booking, only a recorded yes reaches `cancel_appointment` or `reschedule_appointment`, and a no or a change goes back to choosing the appointment or the time. Neither write takes arguments, and each runs under a key made for its Read-back. A new time taken in the meantime gets an apology and new offers.

All three writes go through `agent/src/clinic_agent/writes.py`. A `failed` write is sent once more with the same key. After an `unknown` one the agent reads the EHR before saying anything: the Patient's appointments, and for a Cancel the Slot too. If the write is fully in place it counts as done. Otherwise it is sent once more, which also finishes a half-applied write. A write that answered `unknown` may still land later, so before the agent gives it up as failed it releases it. For a Book or Reschedule, `POST /slots/<slotId>/release` frees the Slot it may have left held. For a Cancel, `POST /appointments/<appointmentId>/cancel/release` changes the Appointment. Only then does it say nothing was done, and a Book's or Reschedule's Callback Request says the Slot is not held. When the second attempt still fails, or the EHR can't be read to confirm it, the agent makes a Handoff with a Callback Request that says which appointment it was about. It tells the Caller it couldn't book, move or cancel, or that it couldn't confirm, and never that it did. Any tool that calls the EHR speaks a holding line after a second without an answer, and a short reminder every five seconds after that.

The server needs the local EHR stack running and reaches the adapter at `EHR_ADAPTER_URL`, `http://localhost:3000` by default.

A Handoff files a Callback Request through the adapter and tells the Caller staff will call back. It fires on a request for a person, a Proxy Caller, a new patient, a clinical question, a second failed verification, and a spelled name that still matches more than one Patient. An emergency mention triggers an Emergency Redirect from any state, before or after verification: the agent says to hang up and dial 911, files an emergency Callback Request and ends the call. Both tools are offered in every node as flow-wide functions, as is `get_clinic_info`, which answers Clinic Questions from the static config in `clinic.py`. If a Callback Request can't be saved, the Caller is told so instead of being promised a callback.

The number a Callback Request calls back is the call's caller ID. On a phone call that is Twilio's `From`: `/voice` passes it to the media stream as a `from_number` stream parameter. A browser call has none, so the agent asks for one when it needs it. In a Handoff it asks before filing, reads the number back, and files only after the Caller says it is right; a no asks again. In an Emergency Redirect the 911 line always comes first. Only then does the agent ask, if the Caller can, for a number where staff can reach them later, and it files that number as soon as it hears it, with no read-back, so nobody in an emergency is kept on the line. A Caller who hangs up instead gets no Callback Request. Nothing is ever filed with a made-up number. The callback number is never used to verify anyone.

You need `DEEPGRAM_API_KEY` and the key for the LLM you pick in `.env`. `LLM_CONFIG=haiku`, the default, uses Claude Haiku 5.5 with thinking off and `ANTHROPIC_API_KEY`. Its system prompt leaves out Pipecat's async-tool guidance, which made it talk before a Handoff. `LLM_CONFIG=haiku-4-5` uses Claude Haiku 4.5 with the same key. `LLM_CONFIG=gemini` uses Gemini 3.6 Flash and `OPENROUTER_API_KEY`. With both Langfuse keys set, every call is traced to Langfuse Cloud. Twilio is optional: the phone routes are on only when both `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` are set, and the server refuses to start with only one of them.

The server listens on `127.0.0.1:8765`. Set `PORT` to change it and `LOG_LEVEL=DEBUG` to see every frame.

## In the browser

With the local EHR stack up, start the server:

```
cd agent
uv run --env-file ../.env clinic-voice-server
```

1. Open `http://localhost:8765` in Chrome, Edge or Firefox. It redirects to `/client/`, Pipecat's prebuilt page. Use `localhost`, not your machine's network address: browsers only offer the microphone to `localhost` or HTTPS pages.
2. Leave the transport menu at the top on SmallWebRTC and click Connect.
3. The browser asks to use your microphone. Allow it. Use headphones, or the agent may hear itself and cut itself off.
4. The agent greets you. Talk as a Caller would. The Conversation panel shows both sides as text, and the Events panel at the bottom logs what the page and the server send each other.
5. Click Disconnect to hang up. The agent also hangs up by itself after a Handoff or an Emergency Redirect. The page pings the server every second, so a closed tab or a sleeping laptop ends the call 5 seconds after its last ping.

The message box under the conversation sends a typed line instead of speech. It skips VAD and STT, so a typed turn gets no latency breakdown. It helps when no microphone is at hand, and the agent still speaks its answers.

The page and the server talk WebRTC directly, over host candidates on your machine, with no STUN or TURN server. Pipecat's `SmallWebRTCTransport` from the `webrtc` extra carries the audio over aiortc, and the page comes from the `pipecat-ai-prebuilt` package. A browser call is traced to Langfuse like a phone call, as one session named by the session id the page started with.

## Over the phone

Start the server with the Twilio keys set, then the tunnel in a second terminal:

```
ngrok config add-authtoken <NGROK_AUTHTOKEN>   # once
ngrok http 8765
```

Your free ngrok account has one static domain: `ngrok http 8765 --domain <your-domain>` keeps the URL the same between runs, so the Twilio setting below never changes.

In the Twilio Console, open Phone Numbers, then Active numbers, then your number. Under Voice Configuration, set "A call comes in" to Webhook, `https://<your-ngrok-domain>/voice`, HTTP POST, and save. The trial account only takes calls from verified numbers and plays a trial notice before the agent picks up.

Twilio signs each webhook with your auth token over the exact URL in that setting. ngrok ends TLS and forwards plain HTTP to the server, but it keeps the public host in the `Host` header, so the server rebuilds `https://<Host>/voice` and checks the `X-Twilio-Signature` header against it. A request without a valid signature gets a 403 and no TwiML, and the server logs the URL it checked. If real calls get a 403, compare that URL with the console setting: it must be `https`, with no port and no trailing slash, and ngrok must not rewrite the host header, so leave out `--host-header`. `TWILIO_AUTH_TOKEN` must be the primary auth token of the account that owns the number.

## Reading latency

The server log prints where each response's time went, from the moment VAD decides the Caller stopped talking to the agent's first audio leaving the server. One block per turn, shaped like this, with made-up numbers:

```
INFO | clinic_agent.server:on_latency_breakdown - Response latency:
 0.200s  endpointing wait     [config: VAD stop_secs]
 0.180s  transcription        [DeepgramSTTService#0]
 0.420s  LLM inference        [AnthropicLLMService#0]
 0.250s  speech synthesis     [DeepgramTTSService#0]
 1.050s  TOTAL
```

The TOTAL line is the voice-to-voice latency as the server sees it. The first block of a call ends `TOTAL (from client connected)` and measures the wait for the greeting, as does the `Greeting started ...s after the call connected` line. What the server can't see is the audio path on either side of it: the browser's capture and playback buffers, or the phone network on a phone call, which add more. A browser call on the same machine has the shortest such path, so its numbers are the closest to the agent's own latency.

In Langfuse, each call is one trace session, named by its Twilio CallSid or the browser's session id. The `conversation` span holds one `turn` span per exchange, with STT, LLM and TTS spans under it. Their `metrics.ttfb` attributes give time to first byte, and `turn.user_bot_latency_seconds` gives the same voice-to-voice latency as the log.

Names, dates of birth and phone numbers are masked before anything is logged or exported to Langfuse. Everything the Caller says before Identity Verification is masked whole, so a trace shows `[UNVERIFIED CALLER]` there. Every attempt to write to the EHR is appended to an audit log, `audit-log.jsonl` in the server's working directory unless `AUDIT_LOG_PATH` names another file. [docs/phi-policy.md](../docs/phi-policy.md) says what is stored where, what the masking covers and misses, and which vendors would need a BAA. Say only synthetic names and dates on test calls.

To hear how a call handles a slow or broken EHR, start the stack with a fault for every request, such as `INJECT_FAULT=timeout docker compose -f infra/compose.yaml up -d --wait --build`. Every write then runs to the adapter's deadline and comes back `unknown`.

Agent tests need no API keys and no audio. The voice server tests play Twilio's side of the media stream, with a scripted Caller in place of Deepgram, and run calls through to a booked Appointment. The browser tests play the page's side with an aiortc peer on the same machine: they start a session, send the WebRTC offer, hear the greeting come back over WebRTC, and run a Handoff that asks for a callback number through to the Callback Request. The conversation tests run whole calls as typed text through the same flow, against the real adapter and HAPI, so start the local EHR stack first. The failure tests put a small proxy in front of the adapter that adds the fault header to the requests a test picks. Set `FHIR_BASE_URL` and `EHR_ADAPTER_URL` when they don't listen on ports 8080 and 3000:

```
cd agent
uv run pytest
```

A scripted fake plays the LLM by default. `--llm haiku` or `--llm gemini` runs the same calls against the real model, with its key from `.env`. Tests that need the fake to force a move a real model wouldn't make are skipped then:

```
uv run --env-file ../.env pytest --llm haiku
```

A conversation test opens a `TextCall` with what the fake LLM should do, speaks Caller lines, and checks what the agent said, the tools it called and was offered, the state the call reached, and the FHIR records:

```python
async with start_call(["What is your full name and date of birth?", verify("Rosalind", "Okonkwo", born)]) as call:
    await call.say("I'd like to book an appointment.")
    await call.say("Rosalind Okonkwo, March 3, 1961.")
    assert call.tool_results("verify_patient") == [{"status": "verified"}]
    assert call.state == "intent"
```
