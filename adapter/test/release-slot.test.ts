// Release slot: once a write has failed, make sure it holds its Slot no longer and never will, even
// if part of it landed or it is still on its way to the EHR.

import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import { startHeldEhr } from "./support/held-ehr.ts";
import {
  appointmentsInSlot,
  clinicTime,
  createPatient,
  createProvider,
  createSlot,
  readAppointment,
  readSlot,
  unusedBirthDate,
  type TestProvider,
} from "./support/ehr.ts";

let adapter: Adapter;
let patientId: string;
let provider: TestProvider;
beforeAll(async () => {
  adapter = await startAdapter();
  patientId = await createPatient({ given: ["Rosalind"], family: "Okonkwo", birthDate: await unusedBirthDate() });
  provider = await createProvider({ given: "Imogen", family: "Faraday" });
});
afterAll(() => adapter.close());

const book = (slotId: string, idempotencyKey: string, headers: Record<string, string> = {}) =>
  adapter.post("/appointments", { patientId, slotId, visitType: "sick_visit", idempotencyKey }, headers);

const release = (slotId: string, idempotencyKey: string) =>
  adapter.post(`/slots/${slotId}/release`, { idempotencyKey });

describe("Release slot", () => {
  test("frees a Slot that a Book which landed in part left held", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "09:00"));
    const idempotencyKey = randomUUID();
    expect((await book(slotId, idempotencyKey, { "x-inject-fault": "half_write" })).body).toEqual({ outcome: "unknown" });
    expect((await readSlot(slotId)).status).toBe("busy");

    const response = await release(slotId, idempotencyKey);

    expect(response.status).toBe(200);
    expect(response.body).toEqual({ outcome: "succeeded" });
    expect((await readSlot(slotId)).status).toBe("free");
    expect(await appointmentsInSlot(slotId)).toEqual([]);
  });

  test("stops a Book still on its way to the EHR from ever landing", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "09:30"));
    const idempotencyKey = randomUUID();
    const heldEhr = await startHeldEhr({ applyAfterGivingUp: true });
    const impatient = await startAdapter({ fhirBaseUrl: heldEhr.url, requestDeadlineMs: 2_000 });
    try {
      const sent = impatient.post("/appointments", { patientId, slotId, visitType: "sick_visit", idempotencyKey });
      await heldEhr.held;
      expect((await sent).body).toEqual({ outcome: "unknown" });

      const response = await release(slotId, idempotencyKey);
      const heldBook = await heldEhr.release();

      expect(response.body).toEqual({ outcome: "succeeded" });
      expect(heldBook).toBe(409);
      expect(await appointmentsInSlot(slotId)).toEqual([]);
      expect((await readSlot(slotId)).status).toBe("free");
    } finally {
      await impatient.close();
      await heldEhr.close();
    }
  });

  test("leaves a Book that landed in its Slot", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "10:00"));
    const idempotencyKey = randomUUID();
    const booked = await book(slotId, idempotencyKey);

    const response = await release(slotId, idempotencyKey);

    expect(response.body).toEqual({ outcome: "rejected", reason: "write_landed" });
    expect((await readSlot(slotId)).status).toBe("busy");
    expect((await appointmentsInSlot(slotId)).map((appointment) => appointment.id)).toEqual([
      booked.body.appointment.appointmentId,
    ]);
  });

  test("leaves a Slot someone else booked alone", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "10:30"));
    const theirs = await book(slotId, randomUUID());

    const response = await release(slotId, randomUUID());

    expect(response.body).toEqual({ outcome: "succeeded" });
    expect((await readSlot(slotId)).status).toBe("busy");
    expect((await appointmentsInSlot(slotId)).map((appointment) => appointment.id)).toEqual([
      theirs.body.appointment.appointmentId,
    ]);
  });

  test("frees the new Slot a Reschedule which landed in part left held, leaving the Appointment where it was", async () => {
    const oldSlot = await createSlot(provider, clinicTime(1, "11:00"));
    const appointmentId = (await book(oldSlot, randomUUID())).body.appointment.appointmentId;
    const newSlot = await createSlot(provider, clinicTime(2, "11:00"));
    const idempotencyKey = randomUUID();
    const halfDone = await adapter.post(
      `/appointments/${appointmentId}/reschedule`,
      { patientId, slotId: newSlot, idempotencyKey },
      { "x-inject-fault": "half_write" },
    );
    expect(halfDone.body).toEqual({ outcome: "unknown" });

    const response = await release(newSlot, idempotencyKey);

    expect(response.body).toEqual({ outcome: "succeeded" });
    expect((await readSlot(newSlot)).status).toBe("free");
    expect((await readAppointment(appointmentId)).slot).toEqual([{ reference: `Slot/${oldSlot}` }]);
    expect((await readSlot(oldSlot)).status).toBe("busy");
  });

  test("leaves the new Slot of a Reschedule that landed", async () => {
    const oldSlot = await createSlot(provider, clinicTime(1, "11:30"));
    const appointmentId = (await book(oldSlot, randomUUID())).body.appointment.appointmentId;
    const newSlot = await createSlot(provider, clinicTime(2, "11:30"));
    const idempotencyKey = randomUUID();
    await adapter.post(`/appointments/${appointmentId}/reschedule`, { patientId, slotId: newSlot, idempotencyKey });

    const response = await release(newSlot, idempotencyKey);

    expect(response.body).toEqual({ outcome: "rejected", reason: "write_landed" });
    expect((await readSlot(newSlot)).status).toBe("busy");
    expect((await readAppointment(appointmentId)).slot).toEqual([{ reference: `Slot/${newSlot}` }]);
  });

  test("answers slot_not_found for a Slot that doesn't exist", async () => {
    const response = await release("no-such-slot", randomUUID());

    expect(response.body).toEqual({ outcome: "rejected", reason: "slot_not_found" });
  });
});
