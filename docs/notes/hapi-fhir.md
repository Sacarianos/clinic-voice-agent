# HAPI FHIR notes

How `hapiproject/hapi:v8.12.0-2` behaves where FHIR or the docs would lead you to expect something else. Each item was run against that image on 2026-10-12. The adapter in `adapter/src/` holds the code these facts shaped, and the README's EHR adapter section describes what it promises. [ADR 0001](../adr/0001-ehr-adapter-is-a-separate-typescript-service.md) says why the adapter exists.

## Writes

- A stale version gets 409. That holds for `ifMatch` inside a transaction and for an `If-Match` header on a plain PUT. The OperationOutcome says `HAPI-0989: ... this is not the current version`, and the whole bundle rolls back. `fhir/client.ts` turns every 409 into `conflict`.
- Writers racing on one version: exactly one commits and the rest get 409. A loser's text can be `HAPI-0989` or `HAPI-0823: Error flushing transaction`. Go by the status.
- `ifMatch` takes `W/"3"` and bare `3`. The adapter sends the weak form, built by `guardedPut`.
- HAPI skips a PUT whose content matches the current version, so no new version appears and no other writer's guard fails. `slot-holds.ts` adds an extension to a released Slot for this reason: the release has to change the Slot to stop an in-flight write.
- Sending a committed bundle again fails with 409. Its `ifNoneExist` entry would match, but the Slot guard holds the old version. So Book looks for its idempotency key first and builds the bundle only when the key is new, as `bookedWithKey` in `book.ts` does. Reschedule and Cancel read the current state and decide from that.
- HAPI does not check that an Appointment's Slot is free. An Appointment that points at a busy Slot gets 201. Only a reference to a Slot that does not exist is refused, with 400 `HAPI-1094`. The Slot version guard in the adapter is the only thing between two Callers and one Slot. A seed script or test helper that creates Appointments directly bypasses it.
- An `ifNoneExist` URL that matches two or more resources gets 412 `HAPI-0958`. Give each seeded resource an identifier that no other resource shares.

## Reads

- `family` and `given` search by prefix and ignore case. `family=Smy` finds Smyth. `family:exact` matches the whole name. `verify-patient.ts` searches by `birthdate` alone and compares names in TypeScript, because the surname rule is looser than any HAPI search.
- A search returns 20 entries and a `next` link unless `_count` says otherwise. `searchEach` in `fhir/client.ts` follows the links.
- HAPI reuses identical searches for a while, so a Slot that was just freed or taken can show its old status. Compose and CI set `HAPI_FHIR_REUSE_CACHED_SEARCH_RESULTS_MILLIS=-1`. Give any new HAPI container the same setting.
- Server-assigned ids are UUIDs. Find seeded resources by identifier, with the systems in `fhir/src/clinic_seed/clinic.py`.

## The container

- The image is distroless. It has no shell, curl or wget, and it runs as uid 65532. A fresh named volume belongs to root, so Compose chowns it first in the `hapi-volume-init` service.
- A Docker health check must use exec form, as Compose does with `java -cp /app HealthCheck`. `docker run --health-cmd` and the GitHub Actions `options: --health-cmd` run through `/bin/sh`, so the container stays `starting` forever. CI starts HAPI without health options. The adapter test global setup and the agent's `_ehr_ready` fixture poll `/fhir/metadata` instead.
- The entrypoint is a bare `java` command, so `JAVA_OPTS` does nothing. Set `JAVA_TOOL_OPTIONS`.
- Cold start takes about a minute. The Compose health check gives it 60 s before it counts failures.
- Git Bash on Windows rewrites arguments like `/app` into `C:/Program Files/Git/app`. Set `MSYS_NO_PATHCONV=1` before `docker exec` or `docker run -v`.
