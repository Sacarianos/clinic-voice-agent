# PHI policy

What patient data this project stores, where it goes, how long it stays, and what keeps it out of places it shouldn't be. Terms are defined in [GLOSSARY.md](../GLOSSARY.md).

Every Patient here is synthetic. Synthea generates them, and tests make their own. Real PHI must never enter this system: on test calls, say only synthetic names, dates of birth and numbers.

## What counts as patient data

The code masks three things: names, dates of birth and phone numbers. They identify a Patient on their own, and the Caller says all three on most calls.

A call carries more than that. The reason for a visit, symptoms a Caller mentions, and the Provider and time of an appointment are health information too once they're tied to a person. The code doesn't mask them, which is why the vendor rules below still apply to masked traces.

## Where data is stored, and for how long

| Store | What it holds | Where | How long |
|---|---|---|---|
| EHR | Patients (name, date of birth, phone), Providers, Slots, Appointments, Callback Requests (the number to call back, the reason, the emergency flag, the Patient if verified) | HAPI FHIR in Docker, in the `hapi-data` volume on the developer's machine | Until `docker compose -f infra/compose.yaml down -v` |
| EHR server logs | HAPI's access log, with search parameters such as a date of birth | The `hapi` container's output | Until the container is removed |
| Audit log | One entry per write attempt: no names, dates of birth or transcript text (see below) | A JSON-lines file at `AUDIT_LOG_PATH`, by default `audit-log.jsonl` in the voice server's working directory. Git ignores it | Forever: nothing in the code rotates or deletes it |
| Voice server logs | Masked log lines from the agent, Pipecat, uvicorn, httpx and OpenTelemetry | The server's stderr. The code writes no log file | Wherever the operator sends stderr |
| Traces | Masked spans: per-turn timing, STT transcripts, LLM input and output, tool calls, TTS text | Langfuse Cloud, EU region unless `LANGFUSE_BASE_URL` says otherwise | Langfuse's data retention for the project. The code sets none |
| Adapter logs | Method, path and error type of a failed request. Never a request body | The `adapter` container's output | Until the container is removed |
| Process memory | The live conversation, and the phrases the mask has learned | The voice server process | The call, for the conversation. Up to 10,000 phrases for the mask, oldest dropped first, gone when the process stops |

HAPI's logs are the EHR's own and sit inside the system of record. Everything else in the table must hold no names, dates of birth or phone numbers, apart from the vendors listed further down.

## Masking

`agent/src/clinic_agent/phi.py` holds one process-wide mask, `PHI`. Logs and trace exports pass everything they write through it. It masks in four ways:

1. **Everything the Caller says before Identity Verification succeeds is masked whole**, as `[UNVERIFIED CALLER]`. Before verification there is no telling which words are a name, so each utterance is learned as a phrase and masked wherever it shows up later: in the LLM context, in an STT span, in a tool call. The `PhiRedactionProcessor` sits in the pipeline right after the Caller is heard and teaches the mask each transcript before the LLM sees it. A line typed into the browser page's message box reaches the LLM as a user message instead of a transcript, and is learned the same way. It leaves the frames themselves alone, because the LLM needs the real name and date of birth to verify the Caller.
2. **Values under keys that hold patient data are masked**, as `[NAME]`, `[DOB]` or `[PHONE]`: `given_name`, `family_name`, `date_of_birth`, `birthDate`, `given`, `family`, `telecom`, `phoneNumber`, `caller_phone`, `from_number`, `From` and the like. This works on key-value text, on JSON and on FHIR-shaped data. Each value is learned too, so a name the agent or the Caller repeats later in free text is masked as well. The Caller's phone number is learned when the call starts, or, on a call without caller ID, when the Caller gives one. A call without caller ID has no phone number at all until then, so no placeholder is ever learned as one.
3. **Dates with a year before the current one are masked** as `[DOB]`, in ISO, numeric, written and spoken forms. Every Patient is an adult and no appointment is in a past year, so scheduling dates stay readable in traces.
4. **Phone numbers are masked by their shape**: E.164 (URL-encoded too), US formats, and seven or more digits said as words.

Where it applies:

- **Logs.** `clinic_agent.logs.configure_logging` is the only log setup the server uses. It sends loguru's records (Pipecat logs through loguru) and the standard library's (httpx, uvicorn, OpenTelemetry) to one sink that masks each whole formatted line, exception traceback included. Tracebacks never show local variables' values. uvicorn runs without its own log config so its access log goes through the same sink.
- **Traces.** `clinic_agent.tracing.MaskingSpanExporter` wraps the OTLP exporter. Every span is rebuilt with its attributes, events and status masked before it leaves the process. JSON attributes such as the LLM input are masked as data and stay valid JSON.
- **Adapter.** The adapter logs only a failed request's method, path and error. Its error messages name the FHIR resource type, never search parameters or bodies.

What stays readable: Provider names, appointment days and times, Visit Types, patient ids, Slot and Appointment ids, Twilio's CallSid, and what a Verified Patient says apart from the names, dates and numbers above.

### Known gaps

- A name the LLM speaks before verification finishes, in the same reply that calls `verify_patient`, can reach a DEBUG log line from TTS before that tool call teaches the mask. Its trace span is masked: spans leave in batches a few seconds later, after the tool call. The server logs at INFO unless `LOG_LEVEL` says otherwise.
- After verification, a name the mask hasn't learned is not masked, such as a relative the Caller mentions.
- A phrase the mask has dropped after 10,000 newer ones is not masked any more. That takes hundreds of calls in one process.

## Audit log

`agent/src/clinic_agent/audit.py`. Every write the agent sends to the EHR goes through `writes.write_until_settled`, which records each attempt exactly once, in a `finally`, so an attempt that hits an injected fault, raises or is cancelled is recorded too. Filing a Callback Request is recorded the same way. Each entry is one JSON line:

| Field | Value |
|---|---|
| `time` | When the attempt finished, UTC, ISO 8601 |
| `action` | `book`, `reschedule`, `cancel`, `release_slot` (freeing the Slot of a Book or Reschedule given up as failed), `callback_request` or `emergency_callback_request` |
| `patient_id` | The Verified Patient's EHR id, or null for a Callback Request before verification |
| `idempotency_key` | The key made for the Read-back, the same on every attempt of one write. Null for Callback Requests |
| `appointment_id`, `slot_id` | What the write changed, when known |
| `attempt` | 1 or 2 |
| `outcome` | What the adapter answered: `succeeded`, `rejected`, `failed` or `unknown`. `error` when the attempt raised |
| `reason` | A rejection code such as `slot_taken`, or the error's type. Never free text |
| `reconciled` | After an `unknown`, what re-reading the EHR found: `succeeded`, `failed` or `unknown` |

It never holds names, dates of birth, phone numbers, reasons in words, or anything said on the call. The agent only appends to it. A real deployment would ship it to write-once storage and keep it at least six years, the period HIPAA sets for keeping required documentation.

## Vendors

A browser call's audio, and the live transcript the page shows, pass only between the browser and the server on the same machine.

These services receive call data unmasked, because they do the work:

| Vendor | Receives |
|---|---|
| Twilio | Call audio and the Caller's phone number |
| ngrok | Everything between Twilio and the laptop: the webhook with the Caller's number, and the media stream |
| Deepgram | Call audio, and the transcripts and speech it makes. `mip_opt_out` keeps audio out of its model-improvement program |
| Anthropic, or OpenRouter and Google for Gemini | The whole conversation, name and date of birth included |
| Langfuse | Masked traces, which still hold the rest of the conversation |

This project runs on trial and hobby accounts with synthetic data only. **A real deployment needs a Business Associate Agreement with every vendor that receives patient data**, Langfuse included, or a self-hosted tracer in its place. Masking narrows what reaches the tracer. It is no substitute for a BAA, because masked traces still tie reasons for visits and appointment details to a call.

## Checks

- `agent/tests/test_phi_safety.py` runs whole calls through the real conversation with DEBUG logging and tracing to a local stand-in for Langfuse: a text call that verifies, books and asks for a callback; one that reschedules and cancels through injected faults; and a phone call over the fake Twilio stream. It then searches every log line and every exported byte for the test Patient's name, date of birth and phone number in their common forms.
- `agent/tests/test_phi_mask.py` pins the date and phone formats the mask recognizes, and what it leaves readable.
- `agent/tests/test_audit_log.py` checks one entry per write attempt for retried, half-applied, timed-out and rejected writes, and that no entry holds patient data or anything said on the call.
- `adapter/test/logging.test.ts` checks the adapter logs nothing of a Patient's when requests carrying their data fail in every way it can inject.
