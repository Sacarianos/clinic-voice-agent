# Clinic Voice Agent

A phone agent for a fictional family medicine clinic. Callers can book, reschedule and cancel appointments over a real phone call. The agent reads and writes a mock FHIR EHR filled with synthetic patients.

Work in progress. See [docs/PLAN.md](docs/PLAN.md) for the plan and [GLOSSARY.md](GLOSSARY.md) for the domain language.

All patient data is synthetic. Nothing in this repo touches real PHI.

## Local EHR

One command starts HAPI FHIR and fills it with the fictional clinic (needs Docker only):

```
docker compose -f infra/compose.yaml up -d --wait --build
```

FHIR base URL: `http://localhost:8080/fhir`. Set `HAPI_PORT` first when 8080 is taken, and `-p <name>` to keep worktrees apart. It holds 60 synthetic adult Patients (Synthea v4.0.0, fixed seed), three Providers with a Schedule each, and free 30-minute Slots Monday to Friday, 8 to 5 (America/New_York), from the day you start it through 14 days out. `docker compose -f infra/compose.yaml restart seed` runs the seed again and adds any new days. It never changes existing resources. `docker compose -f infra/compose.yaml down -v` wipes the data. Delete `fhir/output` to regenerate the Patients.

Seed tests run against that stack, through the FHIR API:

```
cd fhir
FHIR_BASE_URL=http://localhost:8080/fhir uv run pytest
```

Copy `.env.example` to `.env` for the API keys later tickets need.

## Phone calls

The voice server lives in `agent/`. Twilio posts each incoming call to `POST /voice`, which answers with TwiML that opens a media stream to `WS /ws`. Every call runs its own Pipecat pipeline: Silero VAD, Deepgram Nova-3, the configured LLM, and Deepgram Aura-2.

You need `DEEPGRAM_API_KEY` and the key for the LLM you pick in `.env`. `LLM_CONFIG=haiku` (the default) uses Claude Haiku 4.5 and `ANTHROPIC_API_KEY`. `LLM_CONFIG=gemini` uses Gemini 3.6 Flash and `OPENROUTER_API_KEY`. With `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` set, the agent can hang up calls itself. With both Langfuse keys set, every call is traced to Langfuse Cloud.

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

In Langfuse, each call is one trace session named by its Twilio CallSid. The `conversation` span holds one `turn` span per exchange, with STT, LLM and TTS spans under it. Their `metrics.ttfb` attributes give time to first byte, and `turn.user_bot_latency_seconds` gives voice-to-voice latency. The server log prints a breakdown of each response's latency too.

Until the PHI masking work lands, traces and debug logs hold the raw transcript. Say only synthetic names and dates on test calls.

Agent tests need no keys and no network:

```
cd agent
uv run pytest
```

## Latency baseline

Not measured yet. After a few real calls on the default config, record the typical LLM time to first token and the rough voice-to-voice latency here.
