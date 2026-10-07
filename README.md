# Clinic Voice Agent

A phone agent for a fictional family medicine clinic. Callers can book, reschedule and cancel appointments over a real phone call. The agent reads and writes a mock FHIR EHR filled with synthetic patients.

Work in progress. See [docs/PLAN.md](docs/PLAN.md) for the plan and [GLOSSARY.md](GLOSSARY.md) for the domain language.

All patient data is synthetic. Nothing in this repo touches real PHI.

## Local EHR

One command starts HAPI FHIR, fills it with the fictional clinic and starts the EHR adapter in front of it. It needs Docker only:

```
docker compose -f infra/compose.yaml up -d --wait --build
```

FHIR base URL: `http://localhost:8080/fhir`. Set `HAPI_PORT` first when 8080 is taken, and `-p <name>` to keep worktrees apart. It holds 60 synthetic adult Patients (Synthea v4.0.0, fixed seed), three Providers with a Schedule each, and free 30-minute Slots Monday to Friday, 8 to 5 (America/New_York), from the day you start it through 14 days out. `docker compose -f infra/compose.yaml restart seed` runs the seed again and adds any new days. It never changes existing resources. `docker compose -f infra/compose.yaml down -v` wipes the data. Delete `fhir/output` to regenerate the Patients.

Seed tests run against that stack, through the FHIR API:

```
cd fhir
FHIR_BASE_URL=http://localhost:8080/fhir uv run pytest
```

## EHR adapter

The agent never talks to FHIR. It calls the adapter in `adapter/`, a small Hono service on `http://localhost:3000` that answers in plain domain shapes. Set `ADAPTER_PORT` when 3000 is taken. See [ADR 0001](docs/adr/0001-ehr-adapter-is-a-separate-typescript-service.md).

`POST /patients/verify` runs Identity Verification:

```
{ "givenName": "Alvaro", "familyName": "Hudson", "dateOfBirth": "1983-12-25" }
```

It answers 200 with one of `{ "status": "verified", "patientId": "..." }`, `{ "status": "ambiguous" }` or `{ "status": "not_verified" }`. The date of birth must match exactly, the surname must sound the same, and the given name must start with the same letter. When several Patients match, an exact surname and then an exact given name narrow them down, so a spelled-out surname like `"S M I T H"` settles an ambiguous match. A not-verified answer never says which part was wrong.

`GET /providers` lists the Providers a Caller can book with, as `{ "providers": [{ "providerId": "whitfield", "providerName": "Dr. Marcus Whitfield" }] }`. The `providerId` is the clinic's own key and never changes.

`GET /slots` finds free Slots, earliest first. Every filter is optional: `providerId`, `from` and `to` (clinic dates as `YYYY-MM-DD`, both included), `partOfDay` (`morning` starts before noon, `afternoon` at noon or later) and `limit` (3 unless set, at most 20). It only offers Slots inside the Booking Window, from now to the end of the day 14 days from today, clinic time (America/New_York). It answers `{ "slots": [{ "slotId", "providerId", "providerName", "start", "end" }], "bookingWindowLastDay": "2026-10-21" }`, with times in clinic wall-clock time and their UTC offset. An unknown `providerId` gets 400 `unknown_provider`.

`GET /slots/<slotId>` reads one Slot as `{ "slot": { "slotId", "providerId", "providerName", "start", "end", "status" } }`, where `status` is `free` or `busy`. An unknown Slot gets 404 `slot_not_found`. The agent uses it to check what a write did.

`POST /appointments` Books a Slot:

```
{ "patientId": "...", "slotId": "...", "visitType": "annual_physical", "idempotencyKey": "<uuid>" }
```

`visitType` is `annual_physical`, `sick_visit` or `follow_up`. Book creates the Appointment and marks the Slot busy in one FHIR transaction, guarded by the Slot's version, so two Callers booking the same Slot at once get one Appointment between them. The key is stored on the Appointment. A retry with the same key returns the original Appointment and writes nothing new.

`GET /appointments?patientId=...` lists the Patient's upcoming appointments, earliest first, as `{ "appointments": [{ "appointmentId", "patientId", "slotId", "providerId", "providerName", "start", "end", "visitType" }] }`. Cancelled appointments and ones that already started are left out.

`POST /appointments/<appointmentId>/reschedule` moves an appointment to a new Slot:

```
{ "patientId": "...", "slotId": "...", "idempotencyKey": "<uuid>" }
```

It changes the same Appointment, frees the old Slot and takes the new one in one FHIR transaction. Every entry is guarded by the version the adapter read, so a Book that takes the new Slot first leaves the Reschedule rejected as `slot_taken` and the Slot with one Appointment. It is idempotent by target state: an Appointment that already holds the new Slot answers `succeeded`.

`POST /appointments/<appointmentId>/cancel` with `{ "patientId": "...", "idempotencyKey": "<uuid>" }` cancels the Appointment and frees its Slot in one transaction. Cancelling an appointment that is already cancelled answers `succeeded`.

Both refuse to change an appointment that already started, and only change an appointment of the `patientId` given. A version conflict makes the adapter read the EHR once more and decide again, so a racing retry of the same write still answers `succeeded`.

Every write answers 200 with one of four outcomes, and the agent acts on each differently:

- `{ "outcome": "succeeded", "appointment": { "appointmentId", "patientId", "slotId", "providerId", "providerName", "start", "end", "visitType" } }`: written. Only now may the agent say so.
- `{ "outcome": "rejected", "reason": "slot_taken" }`: a business rule stopped it and nothing was written. Book's reasons are `slot_taken`, `outside_booking_window` and `slot_not_found`. Reschedule adds `appointment_not_found`, `appointment_cancelled` and `appointment_in_past`. Cancel's are `appointment_not_found` and `appointment_in_past`.
- `{ "outcome": "failed" }`: nothing was written, so a retry with the same key is safe.
- `{ "outcome": "unknown" }`: the request reached HAPI but no answer came back. Read the EHR again before telling the Caller anything.

Each FHIR request gives up after `FHIR_TIMEOUT_MS` (3000 unless set), so a slow EHR turns a write `unknown` instead of holding the call. Keep it below the agent's 5 s timeout on adapter calls.

A write that lands only in part is safe and can be finished. Each write takes its new Slot before pointing an Appointment at it, and ends an Appointment before freeing its Slot, so a half-applied write never leaves a booked Appointment in a free Slot. A Slot a write takes records the write's idempotency key, so the same write sent again treats that busy Slot as its own and completes. Cancelling again frees a Slot still held for the cancelled Appointment.

Fault injection makes HAPI misbehave on purpose. Send `x-inject-fault: <fault>` on any request, or set `INJECT_FAULT=<fault>` to apply it to every request:

- `timeout`: HAPI applies each write but answers after the FHIR timeout. Writes answer `unknown`, and the write is in FHIR.
- `server_error`: HAPI answers every request with a 500 and applies nothing. Writes answer `failed`, reads `ehr_unavailable`.
- `slot_taken`: someone else takes the Slot just before a Book or Reschedule commits. They answer `rejected` with `slot_taken`. Cancel takes no Slot and is unaffected.
- `half_write`: HAPI applies only the first half of a write's transaction and the connection drops. Writes answer `unknown`. Sending the same write again finishes it.

An unknown fault name gets 400 `invalid_request`.

`POST /callback-requests` files a Callback Request as a FHIR Task:

```
{ "phoneNumber": "+15555550123", "reason": "Caller asked to speak to a person", "emergency": false, "patientId": "..." }
```

`patientId` is optional and links the Task to the Patient. It answers 201 with `{ "callbackRequestId": "..." }`. The number and the emergency flag are labelled inputs on the Task (`callback phone number`, `emergency`), and an emergency Task has priority `stat`.

Errors come back as `{ "error": "<code>" }`: `invalid_request` with 400, `not_found` with 404, `ehr_unavailable` with 502 when HAPI can't be reached or fails during a read. Error bodies never echo the request.

Adapter tests start the adapter on a free port and call it over HTTP against the real HAPI. Each test creates its own Patients and deletes them when its file finishes, so they pass on the seeded stack and on an empty HAPI alike, and the seed tests still find exactly the seeded clinic. Needs Node 24 and pnpm:

```
cd adapter
pnpm install
FHIR_BASE_URL=http://localhost:8080/fhir pnpm test
pnpm typecheck
```

Copy `.env.example` to `.env` for the API keys later tickets need.

## Phone calls

The voice server lives in `agent/`. Twilio posts each incoming call to `POST /voice`, which answers with TwiML that opens a media stream to `WS /ws`. Every call runs its own Pipecat pipeline: Silero VAD, Deepgram Nova-3, the configured LLM, and Deepgram Aura-2. Nova-3 gets the three Providers' full names and surnames as keyterms, so it hears names like Szczepanski and Kowalczyk. The phone and the text transport build the same pipeline in `pipeline.py` and run the same conversation flow; only the ends that hear and speak differ. A Caller can talk over anything the agent says, including the greeting and the Read-back.

The conversation is a `pipecat.flows` state machine in `agent/src/clinic_agent/conversation.py`. The agent greets the Caller and runs Identity Verification through the adapter before anything else. A name that matches several Patients gets a request to spell the last name. A failed attempt gets the same failure message whatever didn't match, and a second one ends the call with a Handoff message. Once verified, the call moves on to Intent. Each state offers the LLM only its own tools, so nothing past verification can be reached before it (see [ADR 0003](docs/adr/0003-safety-rules-live-in-the-state-machine.md)).

Booking lives in `agent/src/clinic_agent/booking.py`. From Intent the agent searches for Slots by Provider, days and part of day, and offers two or three. When the Caller picks one and says what the visit is for, `choose_slot` moves to the Read-back, where the agent itself says the Provider, date, time and Visit Type. Only that state offers `book_appointment`, and it takes no arguments: it books exactly what was read back, under an idempotency key made for that Read-back. Asking for another time during the Read-back goes back to the search without starting over. The agent says the appointment is booked only when the adapter answers `succeeded`. A Slot taken in the meantime gets an apology and new offers.

Rescheduling and cancelling live in `agent/src/clinic_agent/appointments.py`. From Intent, `list_appointments` tells the Caller their upcoming appointments. With more than one, the agent asks which. Choosing one to cancel goes to a Read-back of that appointment, the only state that offers `cancel_appointment`. Choosing one to reschedule goes to the same Slot search as booking, and picking a new time goes to a Read-back of the old and new times, the only state that offers `reschedule_appointment`. Neither write takes arguments, and each runs under a key made for its Read-back. A new time taken in the meantime gets an apology and new offers.

All three writes go through `agent/src/clinic_agent/writes.py`. A `failed` write is sent once more with the same key. After an `unknown` one the agent reads the EHR before saying anything: the Patient's appointments, and for a Cancel the Slot too. If the write is fully in place it counts as done. Otherwise it is sent once more, which also finishes a half-applied write. When the second attempt still fails, or the EHR can't be read to confirm it, the agent makes a Handoff with a Callback Request that says which appointment it was about. It tells the Caller it couldn't book, move or cancel, or that it couldn't confirm, and never that it did. Any tool that calls the EHR speaks a holding line after a second without an answer, and a short reminder every five seconds after that.

The server needs the local EHR stack running and reaches the adapter at `EHR_ADAPTER_URL`, `http://localhost:3000` by default.

A Handoff files a Callback Request through the adapter and tells the Caller staff will call back. It fires on a request for a person, a Proxy Caller, a new patient, a clinical question, and a second failed verification. An emergency mention triggers an Emergency Redirect from any state, before or after verification: the agent says to hang up and dial 911, files an emergency Callback Request and ends the call. Both tools are offered in every node as flow-wide functions, as is `get_clinic_info`, which answers Clinic Questions from the static config in `clinic.py`. If a Callback Request can't be saved, the Caller is told so instead of being promised a callback. The number it calls back comes from Twilio's `From`: `/voice` passes it to the media stream as a `from_number` stream parameter. It is never used to verify anyone.

You need `DEEPGRAM_API_KEY` and the key for the LLM you pick in `.env`. `LLM_CONFIG=haiku` (the default) uses Claude Haiku 4.5 and `ANTHROPIC_API_KEY`. `LLM_CONFIG=gemini` uses Gemini 3.6 Flash and `OPENROUTER_API_KEY`. The server also needs `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`: the agent hangs up calls through Twilio's API, and checks that every `/voice` request comes from Twilio. With both Langfuse keys set, every call is traced to Langfuse Cloud.

Start the server, then the tunnel in a second terminal:

```
cd agent
uv run --env-file ../.env clinic-voice-server
```

```
ngrok config add-authtoken <NGROK_AUTHTOKEN>   # once
ngrok http 8765
```

The server listens on `127.0.0.1:8765`. Set `PORT` to change it and `LOG_LEVEL=DEBUG` to see every frame. Your free ngrok account has one static domain: `ngrok http 8765 --domain <your-domain>` keeps the URL the same between runs, so the Twilio setting below never changes.

In the Twilio Console, open Phone Numbers, then Active numbers, then your number. Under Voice Configuration, set "A call comes in" to Webhook, `https://<your-ngrok-domain>/voice`, HTTP POST, and save. The trial account only takes calls from verified numbers and plays a trial notice before the agent picks up.

Twilio signs each webhook with your auth token over the exact URL in that setting. ngrok ends TLS and forwards plain HTTP to the server, but it keeps the public host in the `Host` header, so the server rebuilds `https://<Host>/voice` and checks the `X-Twilio-Signature` header against it. A request without a valid signature gets a 403 and no TwiML, and the server logs the URL it checked. If real calls get a 403, compare that URL with the console setting: it must be `https`, with no port and no trailing slash, and ngrok must not rewrite the host header (no `--host-header` flag). `TWILIO_AUTH_TOKEN` must be the primary auth token of the account that owns the number.

In Langfuse, each call is one trace session named by its Twilio CallSid. The `conversation` span holds one `turn` span per exchange, with STT, LLM and TTS spans under it. Their `metrics.ttfb` attributes give time to first byte, and `turn.user_bot_latency_seconds` gives voice-to-voice latency. The server log prints a breakdown of each response's latency too.

Names, dates of birth and phone numbers are masked before anything is logged or exported to Langfuse. Everything the Caller says before Identity Verification is masked whole, so a trace shows `[UNVERIFIED CALLER]` there. Every attempt to write to the EHR is appended to an audit log, `audit-log.jsonl` in the server's working directory unless `AUDIT_LOG_PATH` names another file. [docs/phi-policy.md](docs/phi-policy.md) says what is stored where, what the masking covers and misses, and which vendors would need a BAA. Say only synthetic names and dates on test calls.

To hear how a call handles a slow or broken EHR, start the stack with a fault for every request, such as `INJECT_FAULT=timeout docker compose -f infra/compose.yaml up -d --wait --build`. Every write then takes the FHIR timeout and comes back `unknown`.

Agent tests need no API keys. The voice server tests play Twilio's side of the media stream, with a scripted Caller in place of Deepgram, and run calls through to a booked Appointment. The conversation tests run whole calls as typed text through the same flow, against the real adapter and HAPI, so start the local EHR stack first. The failure tests put a small proxy in front of the adapter that adds the fault header to the requests a test picks. Set `FHIR_BASE_URL` and `EHR_ADAPTER_URL` when they don't listen on ports 8080 and 3000:

```
cd agent
uv run pytest
```

A scripted fake plays the LLM by default. `--llm haiku` (or `--llm gemini`) runs the same calls against the real model, with its key from `.env`. Tests that need the fake to force a move a real model wouldn't make are skipped then:

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

## Latency baseline

Not measured yet. After a few real calls on the default config, record the typical LLM time to first token and the rough voice-to-voice latency here.
