# Local EHR

Part of the [Clinic Voice Agent](../README.md). This folder seeds the mock EHR the agent reads and writes.


One command starts HAPI FHIR, fills it with the fictional clinic and starts the EHR adapter in front of it. It needs Docker only:

```
docker compose -f infra/compose.yaml up -d --wait --build
```

FHIR base URL: `http://localhost:8080/fhir`. Set `HAPI_PORT` first when 8080 is taken, and `-p <name>` to keep worktrees apart. It holds 60 synthetic adult Patients that Synthea v4.0.0 makes from a fixed seed, three Providers with a Schedule each, and free 30-minute Slots Monday to Friday, 8 to 5 America/New_York time, from the day you start it through 14 days out. `docker compose -f infra/compose.yaml restart seed` runs the seed again and adds any new days. It never changes existing resources. `docker compose -f infra/compose.yaml down -v` wipes the data. Delete `fhir/output` to regenerate the Patients.

`clinic.json` holds the clinic's time zone, opening hours, Slot length, Booking Window and Providers. The seed, the adapter and the agent all read it, and Compose mounts it into the seed and adapter containers at `/clinic.json`.

Seed tests run against that stack, through the FHIR API. They look only at what the seed made, found by the clinic's identifiers, so other suites' records and leftovers from a crashed run don't affect them. They seed again first, which adds today's days to a stack started earlier. CI runs them against a stack it starts the same way:

```
cd fhir
FHIR_BASE_URL=http://localhost:8080/fhir uv run pytest
```
