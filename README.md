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

Errors come back as `{ "error": "<code>" }`: `invalid_request` with 400, `not_found` with 404, `ehr_unavailable` with 502 when HAPI can't be reached or fails. Error bodies never echo the request.

Adapter tests start the adapter on a free port and call it over HTTP against the real HAPI. Each test creates its own Patients, so they pass on the seeded stack and on an empty HAPI alike. Needs Node 24 and pnpm:

```
cd adapter
pnpm install
FHIR_BASE_URL=http://localhost:8080/fhir pnpm test
pnpm typecheck
```

Copy `.env.example` to `.env` for the API keys later tickets need.
