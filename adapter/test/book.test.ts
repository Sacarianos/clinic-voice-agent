import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import {
  appointmentsInSlot,
  appointmentsWithKey,
  clinicIso,
  clinicTime,
  createPatient,
  createProvider,
  createSlot,
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

const book = (body: unknown) => adapter.post("/appointments", body);

describe("Book", () => {
  test("creates the Appointment with its Visit Type and marks the Slot busy", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "09:00"));
    const idempotencyKey = randomUUID();

    const response = await book({ patientId, slotId, visitType: "annual_physical", idempotencyKey });

    expect(response.status).toBe(200);
    expect(response.body).toEqual({
      outcome: "succeeded",
      appointment: {
        appointmentId: expect.any(String),
        patientId,
        slotId,
        providerId: provider.providerId,
        providerName: "Dr. Imogen Faraday",
        start: clinicTime(1, "09:00"),
        end: clinicTime(1, "09:30"),
        visitType: "annual_physical",
      },
    });

    const [appointment, ...others] = await appointmentsInSlot(slotId);
    expect(others).toEqual([]);
    expect(appointment).toMatchObject({
      id: response.body.appointment.appointmentId,
      status: "booked",
      identifier: [{ system: "https://clinic.example/fhir/identifier/idempotency-key", value: idempotencyKey }],
      appointmentType: { coding: [{ code: "annual_physical" }] },
      slot: [{ reference: `Slot/${slotId}` }],
    });
    expect(appointment!.participant.map((participant) => participant.actor?.reference)).toContain(
      `Patient/${patientId}`,
    );
    expect(Date.parse(appointment!.start!)).toBe(Date.parse(clinicTime(1, "09:00")));
    expect((await readSlot(slotId)).status).toBe("busy");
  });

  test("a retry with the same idempotency key returns the original result and creates nothing new", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "10:00"));
    const request = { patientId, slotId, visitType: "sick_visit", idempotencyKey: randomUUID() };

    const first = await book(request);
    const retry = await book(request);

    expect(first.body.outcome).toBe("succeeded");
    expect(retry.body).toEqual(first.body);
    expect(await appointmentsWithKey(request.idempotencyKey)).toHaveLength(1);
  });

  test("concurrent retries with the same idempotency key all succeed with one Appointment", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "10:30"));
    const request = { patientId, slotId, visitType: "follow_up", idempotencyKey: randomUUID() };

    const responses = await Promise.all([book(request), book(request), book(request)]);

    const [appointment, ...others] = await appointmentsInSlot(slotId);
    expect(others).toEqual([]);
    for (const response of responses) {
      expect(response.body).toMatchObject({ outcome: "succeeded", appointment: { appointmentId: appointment!.id } });
    }
  });

  test("two concurrent Books of the same Slot make exactly one Appointment, and the other is rejected", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "11:00"));
    const otherPatientId = await createPatient({ given: ["Theodora"], family: "Abernathy", birthDate: await unusedBirthDate() });

    const responses = await Promise.all(
      [patientId, otherPatientId].map((patient) =>
        book({ patientId: patient, slotId, visitType: "sick_visit", idempotencyKey: randomUUID() }),
      ),
    );

    expect(responses.map((response) => response.body.outcome).sort()).toEqual(["rejected", "succeeded"]);
    expect(responses.find((response) => response.body.outcome === "rejected")!.body).toEqual({
      outcome: "rejected",
      reason: "slot_taken",
    });
    const appointments = await appointmentsInSlot(slotId);
    expect(appointments.map((appointment) => appointment.id)).toEqual([
      responses.find((response) => response.body.outcome === "succeeded")!.body.appointment.appointmentId,
    ]);
  });

  test("a Slot that is already busy is rejected as taken", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "11:30"), "busy");

    const response = await book({ patientId, slotId, visitType: "sick_visit", idempotencyKey: randomUUID() });

    expect(response.status).toBe(200);
    expect(response.body).toEqual({ outcome: "rejected", reason: "slot_taken" });
    expect(await appointmentsInSlot(slotId)).toEqual([]);
  });

  test.each([
    ["already started", () => clinicIso(new Date(Date.now() - 5 * 60_000))],
    ["starts after the Booking Window", () => clinicTime(15, "09:00")],
  ])("a Slot that %s is rejected as outside the Booking Window", async (_, start) => {
    const slotId = await createSlot(provider, start());

    const response = await book({ patientId, slotId, visitType: "follow_up", idempotencyKey: randomUUID() });

    expect(response.body).toEqual({ outcome: "rejected", reason: "outside_booking_window" });
    expect(await appointmentsInSlot(slotId)).toEqual([]);
    expect((await readSlot(slotId)).status).toBe("free");
  });

  test("a Slot that doesn't exist is rejected", async () => {
    const response = await book({ patientId, slotId: randomUUID(), visitType: "follow_up", idempotencyKey: randomUUID() });

    expect(response.body).toEqual({ outcome: "rejected", reason: "slot_not_found" });
  });

  test.each([
    ["an unknown Visit Type", { visitType: "massage" }],
    ["a missing idempotency key", { idempotencyKey: undefined }],
    ["a blank Slot", { slotId: "" }],
  ])("%s is an invalid request", async (_, change) => {
    const response = await book({ patientId, slotId: "1", visitType: "follow_up", idempotencyKey: randomUUID(), ...change });

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: "invalid_request" });
  });
});
