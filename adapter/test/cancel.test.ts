import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import {
  clinicIso,
  clinicTime,
  createAppointment,
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

const cancel = (appointmentId: string, body: Record<string, unknown> = {}) =>
  adapter.post(`/appointments/${appointmentId}/cancel`, { patientId, idempotencyKey: randomUUID(), ...body });

describe("Cancel", () => {
  test("ends the Appointment and frees its Slot", async () => {
    const { slotId, appointmentId } = await booked(clinicTime(1, "09:00"));

    const response = await cancel(appointmentId);

    expect(response.status).toBe(200);
    expect(response.body).toEqual({
      outcome: "succeeded",
      appointment: {
        appointmentId,
        patientId,
        slotId,
        providerId: provider.providerId,
        providerName: "Dr. Imogen Faraday",
        start: clinicTime(1, "09:00"),
        end: clinicTime(1, "09:30"),
        visitType: "sick_visit",
      },
    });
    expect((await readAppointment(appointmentId)).status).toBe("cancelled");
    expect((await readSlot(slotId)).status).toBe("free");
    expect((await adapter.get("/appointments", { patientId })).body.appointments).toEqual([]);
  });

  test("cancelling twice returns succeeded both times", async () => {
    const { slotId, appointmentId } = await booked(clinicTime(1, "10:00"));

    const first = await cancel(appointmentId);
    const second = await cancel(appointmentId);

    expect(first.body.outcome).toBe("succeeded");
    expect(second.body).toEqual(first.body);
    expect((await readSlot(slotId)).status).toBe("free");
  });

  test("cancelling again after someone else booked the freed Slot leaves their booking alone", async () => {
    const { slotId, appointmentId } = await booked(clinicTime(1, "12:00"));
    await cancel(appointmentId);
    const otherPatientId = await createPatient({ given: ["Theodora"], family: "Abernathy", birthDate: await unusedBirthDate() });
    const theirs = await adapter.post("/appointments", {
      patientId: otherPatientId,
      slotId,
      visitType: "follow_up",
      idempotencyKey: randomUUID(),
    });

    const again = await cancel(appointmentId);

    expect(again.body.outcome).toBe("succeeded");
    expect((await readSlot(slotId)).status).toBe("busy");
    expect((await readAppointment(theirs.body.appointment.appointmentId)).status).toBe("booked");
  });

  test("concurrent Cancels of the same Appointment all succeed", async () => {
    const { slotId, appointmentId } = await booked(clinicTime(1, "10:30"));

    const responses = await Promise.all([cancel(appointmentId), cancel(appointmentId), cancel(appointmentId)]);

    expect(responses.map((response) => response.body.outcome)).toEqual(["succeeded", "succeeded", "succeeded"]);
    expect((await readAppointment(appointmentId)).status).toBe("cancelled");
    expect((await readSlot(slotId)).status).toBe("free");
  });

  test("an appointment that already started is rejected and left as it is", async () => {
    const slotId = await createSlot(provider, clinicIso(new Date(Date.now() - 10 * 60_000)), "busy");
    const appointmentId = await createAppointment(patientId, slotId);

    const response = await cancel(appointmentId);

    expect(response.body).toEqual({ outcome: "rejected", reason: "appointment_in_past" });
    expect((await readAppointment(appointmentId)).status).toBe("booked");
    expect((await readSlot(slotId)).status).toBe("busy");
  });

  test("another Patient's appointment is rejected as not found and left as it is", async () => {
    const { slotId, appointmentId } = await booked(clinicTime(1, "11:00"));
    const otherPatientId = await createPatient({ given: ["Theodora"], family: "Abernathy", birthDate: await unusedBirthDate() });

    const response = await cancel(appointmentId, { patientId: otherPatientId });

    expect(response.body).toEqual({ outcome: "rejected", reason: "appointment_not_found" });
    expect((await readAppointment(appointmentId)).status).toBe("booked");
    expect((await readSlot(slotId)).status).toBe("busy");
  });

  test("an appointment that doesn't exist is rejected as not found", async () => {
    const response = await cancel(randomUUID());

    expect(response.body).toEqual({ outcome: "rejected", reason: "appointment_not_found" });
  });

  test("a missing idempotency key is an invalid request", async () => {
    const { appointmentId } = await booked(clinicTime(1, "11:30"));

    const response = await cancel(appointmentId, { idempotencyKey: undefined });

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: "invalid_request" });
  });
});
