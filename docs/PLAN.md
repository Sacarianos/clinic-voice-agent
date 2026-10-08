# Clinic Voice Agent: Plan

A phone agent for a fictional clinic. It books, reschedules and cancels appointments over a real phone call, reads and writes a mock EHR, handles failures mid-call, keeps PHI out of logs, and is measured with an eval harness. Terms used here are defined in [GLOSSARY.md](../GLOSSARY.md).

## Goals

The project exists to learn four things by doing them:

- Real-time voice: telephony, streaming audio, latency, failing gracefully mid-call.
- Writing into a system of record, including writes that don't cleanly succeed.
- Owning quality: testing and measuring, and knowing when the agent breaks.
- HIPAA hygiene: knowing why certain data can't be logged, and enforcing it in code.

## Scope

In scope:
- Inbound calls: Identity Verification, Book, Reschedule, Cancel, Clinic Questions.
- Mock EHR with synthetic adult patients only. No real PHI anywhere, ever.
- A text-mode harness for development and evals, plus real phone calls at milestone checkpoints.
- An LLM comparison.

Out of scope:
- Real EHR integration, production deployment, multi-tenancy.
- Any frontend beyond the Langfuse UI.
- Proxy Callers, new patients, children, and clinical questions. All of these get a Handoff.

## The clinic

- One location. Family medicine. Open Monday to Friday, 8 to 5.
- Three providers: two physicians and one nurse practitioner. At least one has a surname STT is likely to mangle.
- Every slot is 30 minutes. Visit Type is a label: annual physical, sick visit or follow-up.
- Booking Window: now until two weeks out. Past appointments can't be changed. No cancellation-notice policy.

## Architecture

```
Caller's phone
  <-> Twilio: phone number, Media Streams over WebSocket
  <-> FastAPI server
        POST /voice : Twilio webhook, returns TwiML <Connect><Stream>
        WS   /ws    : one Pipecat pipeline per call
  Pipecat pipeline:
    transport in, Silero VAD -> Deepgram Nova-3 STT -> user context
      -> LLM, Claude Haiku 5.5 by default and swappable
      -> Deepgram Aura-2 TTS -> transport out -> assistant context
    pipecat.flows: conversation state machine
    Custom processors: PHI redaction
  Text transport: same flows with STT and TTS swapped for typed lines
  Tools -> EHR adapter in TypeScript -> HAPI FHIR, seeded with Synthea patients
  Traces -> Langfuse Cloud over OpenTelemetry
  Audit log with no raw PHI
```

### Conversation states

1. **Greet**: say who we are and ask how we can help.
2. **Verify identity**: name and date of birth. No other tools in this state.
3. **Intent**: Book, Reschedule, Cancel, Clinic Question, or Handoff.
4. **Find slot**: fetch free slots and offer two or three.
5. **Read-back**: say the exact details and get a clear yes.
6. **Write**: call the tool and wait for a confirmed result.
7. **Wrap up**: say what happened and end the call.
8. **Handoff** and **Emergency Redirect**: reachable from any state.

Identity Verification rules:
- Date of birth matches exactly. The surname matches by sound, or with one wrong letter per five as long as the first letter is right. The given name matches by its first letter.
- If more than one patient matches, ask the caller to spell their last name.
- Two failed attempts lead to a Handoff.
- The failure message is always the same, so a caller can't learn whether a patient exists.
- Caller ID is never used for verification.

The state machine enforces the safety rules in code. See [ADR 0003](adr/0003-safety-rules-live-in-the-state-machine.md).

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Agent language | Python 3.12 with uv | Agent and evals |
| Adapter language | TypeScript on Node with pnpm | See [ADR 0001](adr/0001-ehr-adapter-is-a-separate-typescript-service.md) |
| Server | FastAPI and Uvicorn | Webhook and WebSocket |
| Voice pipeline | pipecat-ai 1.12 | Flows live in core as `pipecat.flows`. Use `PipelineWorker`, not `PipelineTask` |
| Turn detection | Silero VAD | Tune the silence threshold |
| Telephony | Twilio trial | Only verified numbers can call in. Stays on trial |
| STT | Deepgram Nova-3 | Keyterm prompting with provider names |
| LLM default | Claude Haiku 5.5, `claude-haiku-5-5`, thinking off | Anthropic direct. Swappable in config. Haiku 4.5 stays as the `haiku-4-5` config |
| LLM comparison | Gemini 3.6 Flash via OpenRouter | `google/gemini-3.6-flash` |
| TTS | Deepgram Aura-2 | Same Deepgram account |
| Mock EHR | HAPI FHIR in Docker | Patient records only, no clinical history |
| Synthetic data | Synthea, run in a Docker container | About 60 adults |
| EHR adapter | Hono with zod | Fault injection built in |
| Tracing | Langfuse Cloud, Hobby tier | Self-hosting needs 16 GiB RAM on its own |
| Tunnel | ngrok | Twilio reaches the laptop |
| Orchestration | Docker Compose | HAPI and the adapter |
| Tests and CI | pytest, GitHub Actions | |

## Milestones

Order of work: M0, M1, M2, M3, slim M5, light M4, slim M6. Anything after that is a bonus.

### M0: Setup
- [ ] Accounts for Twilio, Deepgram, ngrok, OpenRouter and Langfuse Cloud. Keys in `.env`, `.env.example` committed
- [ ] `docker compose up` starts HAPI cleanly
- [ ] `GET /Patient?family=...` returns Synthea patients
- [ ] `GET /Slot?status=free` returns seeded slots for the next two weeks
- [ ] Seed scripts are idempotent

### M1: First call
- [ ] Calling the Twilio number gets a spoken greeting and a back-and-forth conversation
- [ ] Interrupting the agent stops it mid-sentence
- [ ] Langfuse shows the conversation with per-turn STT, LLM and TTS timing
- [ ] Baseline noted: typical LLM time to first token and rough voice-to-voice latency

### M2: Real tools and the state machine
- [ ] Adapter endpoints: verify patient, list appointments, find slots, book, reschedule, cancel, create Callback Request
- [ ] Idempotency key on every write, so a retry never double-books
- [ ] Text transport runs the same flows without audio
- [ ] Over a real call: verify, book, and see the appointment in FHIR
- [ ] Over a real call: reschedule, with the old slot freed and the new one taken
- [ ] Scheduling tools can't be reached before verification, covered by a test
- [ ] A Read-back and a yes come before every write

### M3: Failure handling
- [ ] Fault injection for timeout, 500, slot taken between offer and booking, and a write that half succeeds
- [ ] Each fault type has a scripted test and a correct agent response
- [ ] Zero double-bookings under retries
- [ ] The agent never claims success when the write did not succeed
- [ ] No dead air longer than about 2 seconds during slow tools

### M5, slim: Eval harness
- [ ] About 10 scenarios in YAML: patient, goal, twist, expected end state
- [ ] Simulated caller plays the patient. Noise injector garbles some lines from a hand-written list, later extended with errors from real calls, each tagged by source
- [ ] Deterministic graders: FHIR end state, no double-booking, no patient data before verification, say/do match, Handoff when expected
- [ ] One command runs every scenario three times for a given config and pushes scores to Langfuse
- [ ] Results show pass rates per grader, latency P50 and P95, and cost per call

### M4, light: PHI safety
- [ ] A redaction processor masks names, dates of birth and phone numbers before anything reaches logs or traces
- [ ] Audit log: patient ID, action, time, result. No raw transcript
- [ ] Grep of all logs and Langfuse exports for seeded patient names returns zero hits
- [ ] `docs/phi-policy.md` says what is stored, where and for how long, and matches the code. It notes that a real deployment needs a BAA with every vendor or a self-hosted tracer

### M6, slim: Pick the LLM
Decision rule, written before running:
1. Hard gates must be zero: patient data before verification, say/do mismatches, double-bookings.
2. P95 LLM time to first token under about 900 ms.
3. Highest booking accuracy.
4. Lowest cost per call.

With 10 scenarios only large gaps mean anything.

- [ ] Haiku 5.5 vs Haiku 4.5 vs Gemini 3.6 Flash comparison in `docs/results/llm-selection.md`
- [ ] Winner chosen by the rule, with the reasoning

### Dropped for now
- Decision-model router experiment with Jev or Clef-flash.
- Outbound reminder calls.
- Demo recording.
- Stretch: PhoneLLM on Modal, Cartesia vs Aura-2, Spanish.

## Decisions

Decisions that are costly to reverse live in [docs/adr/](adr/). Specs and tickets live in GitHub Issues; the checkboxes above are a summary.
