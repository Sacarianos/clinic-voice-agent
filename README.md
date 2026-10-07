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
