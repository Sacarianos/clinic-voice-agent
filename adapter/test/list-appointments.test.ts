import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import {
  clinicIso,
  clinicTime,
  createAppointment,
  createPatient,
  createProvider,
  createSlot,
  closeSchedule,
  unusedBirthDate,
  type TestProvider,
} from "./support/ehr.ts";

let adapter: Adapter;
let provider: TestProvider;
beforeAll(async () => {
  adapter = await startAdapter();
  provider = await createProvider({ given: "Imogen", family: "Faraday" });
});
afterAll(() => adapter.close());

const book = async (patientId: string, slotId: string, visitType: string) => {
  const response = await adapter.post("/appointments", { patientId, slotId, visitType, idempotencyKey: randomUUID() });
  expect(response.body.outcome).toBe("succeeded");
  return response.body.appointment.appointmentId as string;
};

test("List appointments returns only the Patient's upcoming appointments, earliest first", async () => {
  const patientId = await createPatient({ given: ["Rosalind"], family: "Okonkwo", birthDate: await unusedBirthDate() });
  const otherPatientId = await createPatient({ given: ["Theodora"], family: "Abernathy", birthDate: await unusedBirthDate() });
  const laterSlot = await createSlot(provider, clinicTime(3, "14:00"));
  const soonerSlot = await createSlot(provider, clinicTime(1, "09:00"));
  const later = await book(patientId, laterSlot, "annual_physical");
  const sooner = await book(patientId, soonerSlot, "sick_visit");
  await book(otherPatientId, await createSlot(provider, clinicTime(2, "10:00")), "follow_up");
  await createAppointment(patientId, await createSlot(provider, clinicTime(2, "11:00")), "cancelled");
  await createAppointment(patientId, await createSlot(provider, clinicIso(new Date(Date.now() - 60 * 60_000)), "busy"));

  const response = await adapter.get("/appointments", { patientId });

  expect(response.status).toBe(200);
  expect(response.body).toEqual({
    appointments: [
      {
        appointmentId: sooner,
        patientId,
        slotId: soonerSlot,
        providerId: provider.providerId,
        providerName: "Dr. Imogen Faraday",
        start: clinicTime(1, "09:00"),
        end: clinicTime(1, "09:30"),
        visitType: "sick_visit",
      },
      {
        appointmentId: later,
        patientId,
        slotId: laterSlot,
        providerId: provider.providerId,
        providerName: "Dr. Imogen Faraday",
        start: clinicTime(3, "14:00"),
        end: clinicTime(3, "14:30"),
        visitType: "annual_physical",
      },
    ],
  });
});

test("an Appointment with a Provider who no longer takes new ones is still listed, with that Provider", async () => {
  const leaving = await createProvider({ given: "Ambrose", family: "Kettering" });
  const patientId = await createPatient({ given: ["Rosalind"], family: "Okonkwo", birthDate: await unusedBirthDate() });
  const slotId = await createSlot(leaving, clinicTime(4, "10:00"));
  const appointmentId = await book(patientId, slotId, "follow_up");
  await closeSchedule(leaving);

  const response = await adapter.get("/appointments", { patientId });

  expect(response.status).toBe(200);
  expect(response.body.appointments).toEqual([
    expect.objectContaining({ appointmentId, providerId: leaving.providerId, providerName: "Dr. Ambrose Kettering" }),
  ]);
});

test("a Patient with no upcoming appointments gets an empty list", async () => {
  const patientId = await createPatient({ given: ["Ottoline"], family: "Pemberton", birthDate: await unusedBirthDate() });

  const response = await adapter.get("/appointments", { patientId });

  expect(response.body).toEqual({ appointments: [] });
});

test("listing without a Patient is an invalid request", async () => {
  const response = await adapter.get("/appointments");

  expect(response.status).toBe(400);
  expect(response.body).toEqual({ error: "invalid_request" });
});
