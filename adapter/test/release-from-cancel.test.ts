// Release from Cancel: once a Cancel has failed, make sure it never lands later, even if it is still
// on its way to the EHR. The Cancel counterpart of releasing a Slot from a Book or Reschedule.

import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import { startHeldEhr } from "./support/held-ehr.ts";
import {
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

async function booked(start: string) {
  const slotId = await createSlot(provider, start);
  const response = await adapter.post("/appointments", {
    patientId,
    slotId,
    visitType: "sick_visit",
    idempotencyKey: randomUUID(),
  });
  return { slotId, appointmentId: response.body.appointment.appointmentId as string };
}

const cancel = (appointmentId: string, idempotencyKey: string, headers: Record<string, string> = {}) =>
  adapter.post(`/appointments/${appointmentId}/cancel`, { patientId, idempotencyKey }, headers);

const release = (appointmentId: string, idempotencyKey: string, patient = patientId) =>
  adapter.post(`/appointments/${appointmentId}/cancel/release`, { patientId: patient, idempotencyKey });

describe("Release from Cancel", () => {
  test("stops a Cancel still on its way to the EHR from ever landing", async () => {
    const { slotId, appointmentId } = await booked(clinicTime(1, "09:00"));
    const idempotencyKey = randomUUID();
    const heldEhr = await startHeldEhr({ applyAfterGivingUp: true });
    const impatient = await startAdapter({ fhirBaseUrl: heldEhr.url, requestDeadlineMs: 2_000 });
    try {
      const sent = impatient.post(`/appointments/${appointmentId}/cancel`, { patientId, idempotencyKey });
      await heldEhr.held;
      expect((await sent).body).toEqual({ outcome: "unknown" });

      const response = await release(appointmentId, idempotencyKey);
      const heldCancel = await heldEhr.release();

      expect(response.status).toBe(200);
      expect(response.body).toEqual({ outcome: "succeeded" });
      expect(heldCancel).toBe(409);
      expect((await readAppointment(appointmentId)).status).toBe("booked");
      expect((await readSlot(slotId)).status).toBe("busy");
    } finally {
      await impatient.close();
      await heldEhr.close();
    }
  });

  test("leaves a Cancel that landed", async () => {
    const { slotId, appointmentId } = await booked(clinicTime(1, "09:30"));
    const idempotencyKey = randomUUID();
    await cancel(appointmentId, idempotencyKey);

    const response = await release(appointmentId, idempotencyKey);

    expect(response.body).toEqual({ outcome: "rejected", reason: "write_landed" });
    expect((await readAppointment(appointmentId)).status).toBe("cancelled");
    expect((await readSlot(slotId)).status).toBe("free");
  });

  test("finishes a Cancel that landed in part, so its Slot is free", async () => {
    const { slotId, appointmentId } = await booked(clinicTime(1, "10:00"));
    const idempotencyKey = randomUUID();
    const halfDone = await cancel(appointmentId, idempotencyKey, { "x-inject-fault": "half_write" });
    expect(halfDone.body).toEqual({ outcome: "unknown" });
    expect((await readSlot(slotId)).status).toBe("busy");

    const response = await release(appointmentId, idempotencyKey);

    expect(response.body).toEqual({ outcome: "rejected", reason: "write_landed" });
    expect((await readAppointment(appointmentId)).status).toBe("cancelled");
    expect((await readSlot(slotId)).status).toBe("free");
  });

  test("answers appointment_not_found for another Patient's Appointment", async () => {
    const { appointmentId } = await booked(clinicTime(1, "10:30"));
    const someoneElse = await createPatient({ given: ["Beatrix"], family: "Lindqvist", birthDate: await unusedBirthDate() });

    const response = await release(appointmentId, randomUUID(), someoneElse);

    expect(response.body).toEqual({ outcome: "rejected", reason: "appointment_not_found" });
    expect((await readAppointment(appointmentId)).status).toBe("booked");
  });
});
