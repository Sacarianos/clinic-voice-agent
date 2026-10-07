import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import { startHeldEhr } from "./support/held-ehr.ts";
import {
  appointmentsInSlot,
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

const book = (patient: string, slotId: string) =>
  adapter.post("/appointments", { patientId: patient, slotId, visitType: "annual_physical", idempotencyKey: randomUUID() });

async function booked(start: string) {
  const slotId = await createSlot(provider, start);
  const response = await book(patientId, slotId);
  return { slotId, appointmentId: response.body.appointment.appointmentId as string };
}

const reschedule = (appointmentId: string, slotId: string, body: Record<string, unknown> = {}) =>
  adapter.post(`/appointments/${appointmentId}/reschedule`, {
    patientId,
    slotId,
    idempotencyKey: randomUUID(),
    ...body,
  });

describe("Reschedule", () => {
  test("frees the old Slot, takes the new one and keeps the same Appointment", async () => {
    const { slotId: oldSlot, appointmentId } = await booked(clinicTime(1, "09:00"));
    const { identifier } = await readAppointment(appointmentId);
    const newSlot = await createSlot(provider, clinicTime(2, "14:00"));

    const response = await reschedule(appointmentId, newSlot);

    expect(response.status).toBe(200);
    expect(response.body).toEqual({
      outcome: "succeeded",
      appointment: {
        appointmentId,
        patientId,
        slotId: newSlot,
        providerId: provider.providerId,
        providerName: "Dr. Imogen Faraday",
        start: clinicTime(2, "14:00"),
        end: clinicTime(2, "14:30"),
        visitType: "annual_physical",
      },
    });
    const appointment = await readAppointment(appointmentId);
    expect(appointment).toMatchObject({ status: "booked", identifier, slot: [{ reference: `Slot/${newSlot}` }] });
    expect(Date.parse(appointment.start!)).toBe(Date.parse(clinicTime(2, "14:00")));
    expect(Date.parse(appointment.end!)).toBe(Date.parse(clinicTime(2, "14:30")));
    expect((await readSlot(oldSlot)).status).toBe("free");
    expect((await readSlot(newSlot)).status).toBe("busy");
    expect(await appointmentsInSlot(newSlot)).toHaveLength(1);
  });

  test("to another Provider's Slot moves the Appointment to that Provider", async () => {
    const { appointmentId } = await booked(clinicTime(1, "09:30"));
    const okafor = await createProvider({ given: "Chidi", family: "Okafor" });
    const newSlot = await createSlot(okafor, clinicTime(1, "15:00"));

    const response = await reschedule(appointmentId, newSlot);

    expect(response.body.appointment).toMatchObject({ providerId: okafor.providerId, providerName: "Dr. Chidi Okafor" });
    const participants = (await readAppointment(appointmentId)).participant.map((participant) => participant.actor?.reference);
    expect(participants).toContain(`Patient/${patientId}`);
    expect(participants.filter((reference) => reference?.startsWith("Practitioner/"))).toHaveLength(1);
    expect((await adapter.get("/appointments", { patientId })).body.appointments).toContainEqual(response.body.appointment);
  });

  test("to the Slot the Appointment already holds returns succeeded and changes nothing", async () => {
    const { slotId: oldSlot, appointmentId } = await booked(clinicTime(1, "10:00"));
    const newSlot = await createSlot(provider, clinicTime(2, "10:00"));
    const first = await reschedule(appointmentId, newSlot);
    const version = (await readAppointment(appointmentId)).meta?.versionId;

    const retry = await reschedule(appointmentId, newSlot);

    expect(first.body.outcome).toBe("succeeded");
    expect(retry.body).toEqual(first.body);
    expect((await readAppointment(appointmentId)).meta?.versionId).toBe(version);
    expect((await readSlot(oldSlot)).status).toBe("free");
    expect((await readSlot(newSlot)).status).toBe("busy");
  });

  test("a Book that takes the new Slot while the Reschedule is being written leaves the Reschedule rejected", async () => {
    const { slotId: oldSlot, appointmentId } = await booked(clinicTime(1, "13:30"));
    const target = await createSlot(provider, clinicTime(2, "15:00"));
    const otherPatientId = await createPatient({ given: ["Theodora"], family: "Abernathy", birthDate: await unusedBirthDate() });
    const heldEhr = await startHeldEhr();
    const heldAdapter = await startAdapter({ fhirBaseUrl: heldEhr.url });

    try {
      const moving = heldAdapter.post(`/appointments/${appointmentId}/reschedule`, {
        patientId,
        slotId: target,
        idempotencyKey: randomUUID(),
      });
      await heldEhr.held;
      const booking = await book(otherPatientId, target);
      heldEhr.release();
      const moved = await moving;

      expect(booking.body.outcome).toBe("succeeded");
      expect(moved.body).toEqual({ outcome: "rejected", reason: "slot_taken" });
      const holders = (await appointmentsInSlot(target)).filter((appointment) => appointment.status === "booked");
      expect(holders.map((appointment) => appointment.id)).toEqual([booking.body.appointment.appointmentId]);
      expect((await readAppointment(appointmentId)).slot).toEqual([{ reference: `Slot/${oldSlot}` }]);
      expect((await readSlot(oldSlot)).status).toBe("busy");
    } finally {
      heldEhr.release();
      await heldAdapter.close();
      await heldEhr.close();
    }
  });

  test("a Reschedule that takes the Slot while a Book is being written leaves the Book rejected", async () => {
    const { slotId: oldSlot, appointmentId } = await booked(clinicTime(1, "14:00"));
    const target = await createSlot(provider, clinicTime(2, "15:30"));
    const otherPatientId = await createPatient({ given: ["Ignatius"], family: "Abernathy", birthDate: await unusedBirthDate() });
    const heldEhr = await startHeldEhr();
    const heldAdapter = await startAdapter({ fhirBaseUrl: heldEhr.url });

    try {
      const booking = heldAdapter.post("/appointments", {
        patientId: otherPatientId,
        slotId: target,
        visitType: "sick_visit",
        idempotencyKey: randomUUID(),
      });
      await heldEhr.held;
      const moved = await reschedule(appointmentId, target);
      heldEhr.release();

      expect(moved.body.outcome).toBe("succeeded");
      expect((await booking).body).toEqual({ outcome: "rejected", reason: "slot_taken" });
      const holders = (await appointmentsInSlot(target)).filter((appointment) => appointment.status === "booked");
      expect(holders.map((appointment) => appointment.id)).toEqual([appointmentId]);
      expect((await readSlot(oldSlot)).status).toBe("free");
    } finally {
      heldEhr.release();
      await heldAdapter.close();
      await heldEhr.close();
    }
  });

  test("to a Slot that is already busy is rejected as taken and changes nothing", async () => {
    const { slotId: oldSlot, appointmentId } = await booked(clinicTime(1, "11:00"));
    const busySlot = await createSlot(provider, clinicTime(2, "09:00"), "busy");

    const response = await reschedule(appointmentId, busySlot);

    expect(response.body).toEqual({ outcome: "rejected", reason: "slot_taken" });
    expect((await readAppointment(appointmentId)).slot).toEqual([{ reference: `Slot/${oldSlot}` }]);
    expect((await readSlot(oldSlot)).status).toBe("busy");
  });

  test.each([
    ["already started", () => clinicIso(new Date(Date.now() - 5 * 60_000))],
    ["starts after the Booking Window", () => clinicTime(15, "09:00")],
  ])("to a Slot that %s is rejected as outside the Booking Window", async (_, start) => {
    const { appointmentId } = await booked(clinicTime(1, "11:30"));
    const newSlot = await createSlot(provider, start());

    const response = await reschedule(appointmentId, newSlot);

    expect(response.body).toEqual({ outcome: "rejected", reason: "outside_booking_window" });
    expect((await readSlot(newSlot)).status).toBe("free");
  });

  test("to a Slot that doesn't exist is rejected", async () => {
    const { appointmentId } = await booked(clinicTime(1, "12:00"));

    const response = await reschedule(appointmentId, randomUUID());

    expect(response.body).toEqual({ outcome: "rejected", reason: "slot_not_found" });
  });

  test("an appointment that already started is rejected and left as it is", async () => {
    const oldSlot = await createSlot(provider, clinicIso(new Date(Date.now() - 10 * 60_000)), "busy");
    const appointmentId = await createAppointment(patientId, oldSlot);
    const newSlot = await createSlot(provider, clinicTime(2, "12:00"));

    const response = await reschedule(appointmentId, newSlot);

    expect(response.body).toEqual({ outcome: "rejected", reason: "appointment_in_past" });
    expect((await readAppointment(appointmentId)).slot).toEqual([{ reference: `Slot/${oldSlot}` }]);
    expect((await readSlot(newSlot)).status).toBe("free");
  });

  test("a cancelled appointment is rejected and the new Slot stays free", async () => {
    const { appointmentId } = await booked(clinicTime(1, "12:30"));
    await adapter.post(`/appointments/${appointmentId}/cancel`, { patientId, idempotencyKey: randomUUID() });
    const newSlot = await createSlot(provider, clinicTime(2, "12:30"));

    const response = await reschedule(appointmentId, newSlot);

    expect(response.body).toEqual({ outcome: "rejected", reason: "appointment_cancelled" });
    expect((await readSlot(newSlot)).status).toBe("free");
  });

  test("another Patient's appointment is rejected as not found", async () => {
    const { appointmentId } = await booked(clinicTime(1, "13:00"));
    const otherPatientId = await createPatient({ given: ["Theodora"], family: "Abernathy", birthDate: await unusedBirthDate() });
    const newSlot = await createSlot(provider, clinicTime(2, "13:00"));

    const response = await reschedule(appointmentId, newSlot, { patientId: otherPatientId });

    expect(response.body).toEqual({ outcome: "rejected", reason: "appointment_not_found" });
    expect((await readSlot(newSlot)).status).toBe("free");
  });

  test("a missing Slot is an invalid request", async () => {
    const response = await reschedule("1", "");

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: "invalid_request" });
  });
});
