# EHR adapter

Part of the [Clinic Voice Agent](../README.md). This is the HTTP API reference for the service between the agent and the EHR.


The agent never talks to FHIR. It calls the adapter in `adapter/`, a small Hono service on `http://localhost:3000` that answers in plain domain shapes. Set `ADAPTER_PORT` when 3000 is taken. See [ADR 0001](../docs/adr/0001-ehr-adapter-is-a-separate-typescript-service.md).

`POST /patients/verify` runs Identity Verification:

```
{ "givenName": "Alvaro", "familyName": "Hudson", "dateOfBirth": "1983-12-25" }
```

It answers 200 with one of `{ "status": "verified", "patientId": "..." }`, `{ "status": "ambiguous" }` or `{ "status": "not_verified" }`. The date of birth must match exactly, the surname must sound the same, and the given name must start with the same letter. When several Patients match, the answer is ambiguous, even if one of them has exactly the name sent, because speech recognition can write down one Patient's name for the other. The agent then asks the Caller to spell the surname and sends the next attempt with `"familyNameSpelled": true`. Only then does an exact surname break the tie, so a spelled-out `"S M I T H"` picks Smith over Smyth. A given name never breaks a tie. A not-verified answer never says which part was wrong.

`GET /providers` lists the Providers a Caller can book with, as `{ "providers": [{ "providerId": "whitfield", "providerName": "Dr. Marcus Whitfield" }] }`. The `providerId` is the clinic's own key and never changes.

`GET /slots` finds free Slots, earliest first. Every filter is optional: `providerId`, `from`, `to`, `partOfDay` and `limit`. `from` and `to` are clinic dates as `YYYY-MM-DD`, both included. A `morning` Slot starts before noon and an `afternoon` one at noon or later. `limit` is 3 unless set, and at most 20. It only offers Slots inside the Booking Window, from now to the end of the day 14 days from today, in clinic time, which is America/New_York. It answers `{ "slots": [{ "slotId", "providerId", "providerName", "start", "end" }], "bookingWindowLastDay": "2026-10-21" }`, with times in clinic wall-clock time and their UTC offset. An unknown `providerId` gets 400 `unknown_provider`.

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

The adapter answers every request within `REQUEST_DEADLINE_MS`, 4000 unless set, every FHIR call it makes included. A slow EHR fails a read, and fails a write or turns it `unknown`, instead of holding the call. The agent counts on this deadline, `ADAPTER_DEADLINE_SECS` in its `timeouts` module, and waits a second longer. `GET /healthz` reports it as `requestDeadlineMs`, and an agent test fails when the two differ.

A write that lands only in part is safe and can be finished. Each write takes its new Slot before pointing an Appointment at it, and ends an Appointment before freeing its Slot, so a half-applied write never leaves a booked Appointment in a free Slot. A Slot a write takes records the write's idempotency key, so the same write sent again treats that busy Slot as its own and completes. Cancelling again frees a Slot still held for the cancelled Appointment.

`POST /slots/<slotId>/release` with `{ "idempotencyKey": "<uuid>" }` settles a Book or Reschedule that the agent gives up on after an `unknown` answer. The write may have landed in part, leaving the Slot busy, or may still be on its way to HAPI. Release frees the Slot if that write holds it, and changes the Slot either way, so a write still on its way, guarded by the Slot version it read, can never commit. It answers `succeeded` when the write holds the Slot no longer and never will, and `rejected` with `write_landed` when the write landed in full and holds the Slot with a booked Appointment. An unknown Slot is `rejected` with `slot_not_found`.

`POST /appointments/<appointmentId>/cancel/release` with `{ "patientId": "...", "idempotencyKey": "<uuid>" }` does the same for a Cancel. It changes a booked Appointment, so a Cancel still on its way, guarded by the Appointment version it read, can never commit, and answers `succeeded`: the Appointment stays booked. A Cancel that landed is finished, its Slot freed if only part of it landed, and answers `rejected` with `write_landed`. Another Patient's or an unknown Appointment is `rejected` with `appointment_not_found`.

Fault injection makes HAPI misbehave on purpose. Send `x-inject-fault: <fault>` on any request, or set `INJECT_FAULT=<fault>` to apply it to every request:

- `timeout`: HAPI applies each write but answers only after the adapter's deadline. Writes answer `unknown`, and the write is in FHIR by then.
- `server_error`: HAPI answers every request with a 500 and applies nothing. Writes answer `failed`, reads `ehr_unavailable`.
- `slot_taken`: someone else takes the Slot just before a Book or Reschedule commits. They answer `rejected` with `slot_taken`. Cancel takes no Slot and is unaffected.
- `half_write`: HAPI applies only the first half of a write's transaction and the connection drops. Writes answer `unknown`. Sending the same write again finishes it.
- `stalled_write`: HAPI sits on each write until the adapter's deadline passes, then applies it 3 s later if it still applies. Writes answer `unknown`, and nothing is in FHIR until after that answer.
- `slow`: HAPI answers every request 2 s late, reads included. One FHIR call still fits within the deadline, so a call hears the holding line and the request succeeds.

An unknown fault name gets 400 `invalid_request`.

`POST /callback-requests` files a Callback Request as a FHIR Task:

```
{ "phoneNumber": "+15555550123", "reason": "Caller asked to speak to a person", "emergency": false, "patientId": "..." }
```

`patientId` is optional and links the Task to the Patient. It answers 201 with `{ "callbackRequestId": "..." }`. The number and the emergency flag are inputs on the Task labelled `callback phone number` and `emergency`, and an emergency Task has priority `stat`.

Errors come back as `{ "error": "<code>" }`: `invalid_request` with 400, `not_found` with 404, `ehr_unavailable` with 502 when HAPI can't be reached or fails during a read. Error bodies never echo the request.

Adapter tests start the adapter on a free port and call it over HTTP against the real HAPI. Each test creates its own Patients and deletes them when its file finishes, so they pass on the seeded stack and on an empty HAPI alike, and the seed tests still find exactly the seeded clinic. Needs Node 24 and pnpm:

```
cd adapter
pnpm install
FHIR_BASE_URL=http://localhost:8080/fhir pnpm test
pnpm typecheck
```
