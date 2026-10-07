import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import {
  appointmentsInSlot,
  appointmentsWithKey,
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

const withFault = (fault: string) => ({ "x-inject-fault": fault });

const book = (slotId: string, fault?: string, idempotencyKey = randomUUID()) =>
  adapter.post(
    "/appointments",
    { patientId, slotId, visitType: "sick_visit", idempotencyKey },
    fault ? withFault(fault) : {},
  );

describe("server_error", () => {
  test("fails a Book and writes nothing", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "09:00"));

    const response = await book(slotId, "server_error");

    expect(response.body).toEqual({ outcome: "failed" });
    expect(await appointmentsInSlot(slotId)).toEqual([]);
    expect((await readSlot(slotId)).status).toBe("free");
  });

  test("makes a read answer ehr_unavailable", async () => {
    const response = await adapter.get("/slots", {}, withFault("server_error"));

    expect(response.status).toBe(502);
    expect(response.body).toEqual({ error: "ehr_unavailable" });
  });

  test("set in config, applies to every request", async () => {
    const faulty = await startAdapter({ injectFault: "server_error" });
    try {
      const slotId = await createSlot(provider, clinicTime(1, "09:30"));

      const response = await faulty.post("/appointments", {
        patientId,
        slotId,
        visitType: "sick_visit",
        idempotencyKey: randomUUID(),
      });

      expect(response.body).toEqual({ outcome: "failed" });
    } finally {
      await faulty.close();
    }
  });
});

describe("timeout", () => {
  test("makes a Book unknown, though the EHR applied it", async () => {
    const impatient = await startAdapter({ fhirTimeoutMs: 500 });
    try {
      const slotId = await createSlot(provider, clinicTime(1, "10:00"));
      const idempotencyKey = randomUUID();

      const response = await impatient.post(
        "/appointments",
        { patientId, slotId, visitType: "sick_visit", idempotencyKey },
        withFault("timeout"),
      );

      expect(response.body).toEqual({ outcome: "unknown" });
      const [appointment, ...others] = await appointmentsInSlot(slotId);
      expect(others).toEqual([]);
      expect(appointment?.identifier?.[0]?.value).toBe(idempotencyKey);
      expect((await readSlot(slotId)).status).toBe("busy");
    } finally {
      await impatient.close();
    }
  });
});

describe("stalled_write", () => {
  test("makes a Book unknown, and the Book lands after the adapter answered", async () => {
    const quick = await startAdapter({ fhirTimeoutMs: 2_000 });
    try {
      const slotId = await createSlot(provider, clinicTime(1, "10:15"));
      const idempotencyKey = randomUUID();

      const response = await quick.post(
        "/appointments",
        { patientId, slotId, visitType: "sick_visit", idempotencyKey },
        withFault("stalled_write"),
      );

      expect(response.body).toEqual({ outcome: "unknown" });
      expect(await appointmentsInSlot(slotId)).toEqual([]);
      await expect.poll(() => appointmentsWithKey(idempotencyKey), { timeout: 10_000, interval: 200 }).toHaveLength(1);
      expect((await readSlot(slotId)).status).toBe("busy");
    } finally {
      await quick.close();
    }
  });
});

describe("slot_taken", () => {
  test("makes a Book rejected, with the Slot busy and no Appointment of the Caller's", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "10:30"));

    const response = await book(slotId, "slot_taken");

    expect(response.body).toEqual({ outcome: "rejected", reason: "slot_taken" });
    expect(await appointmentsInSlot(slotId)).toEqual([]);
    expect((await readSlot(slotId)).status).toBe("busy");
  });

  test("makes a Reschedule rejected, leaving the Appointment where it was", async () => {
    const oldSlot = await createSlot(provider, clinicTime(1, "11:00"));
    const appointmentId = (await book(oldSlot)).body.appointment.appointmentId;
    const newSlot = await createSlot(provider, clinicTime(2, "11:00"));

    const response = await adapter.post(
      `/appointments/${appointmentId}/reschedule`,
      { patientId, slotId: newSlot, idempotencyKey: randomUUID() },
      withFault("slot_taken"),
    );

    expect(response.body).toEqual({ outcome: "rejected", reason: "slot_taken" });
    expect((await readAppointment(appointmentId)).slot).toEqual([{ reference: `Slot/${oldSlot}` }]);
    expect((await readSlot(oldSlot)).status).toBe("busy");
  });
});

describe("half_write", () => {
  test("makes a Book unknown, and the same Book sent again finishes it", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "11:30"));
    const idempotencyKey = randomUUID();

    const halfDone = await book(slotId, "half_write", idempotencyKey);

    expect(halfDone.body).toEqual({ outcome: "unknown" });
    expect(await appointmentsInSlot(slotId)).toEqual([]);
    expect((await readSlot(slotId)).status).toBe("busy");

    const retry = await book(slotId, undefined, idempotencyKey);

    expect(retry.body.outcome).toBe("succeeded");
    expect((await appointmentsInSlot(slotId)).map((appointment) => appointment.id)).toEqual([
      retry.body.appointment.appointmentId,
    ]);
    expect((await readSlot(slotId)).status).toBe("busy");
  });

  test("makes a Reschedule unknown without moving the Appointment, and the same Reschedule sent again finishes it", async () => {
    const oldSlot = await createSlot(provider, clinicTime(1, "12:00"));
    const appointmentId = (await book(oldSlot)).body.appointment.appointmentId;
    const newSlot = await createSlot(provider, clinicTime(2, "12:00"));
    const reschedule = (fault?: string) =>
      adapter.post(
        `/appointments/${appointmentId}/reschedule`,
        { patientId, slotId: newSlot, idempotencyKey },
        fault ? withFault(fault) : {},
      );
    const idempotencyKey = randomUUID();

    const halfDone = await reschedule("half_write");

    expect(halfDone.body).toEqual({ outcome: "unknown" });
    expect((await readAppointment(appointmentId)).slot).toEqual([{ reference: `Slot/${oldSlot}` }]);
    // No one else can take the new Slot in the meantime.
    expect((await readSlot(newSlot)).status).toBe("busy");
    expect((await book(newSlot)).body).toEqual({ outcome: "rejected", reason: "slot_taken" });

    const retry = await reschedule();

    expect(retry.body.outcome).toBe("succeeded");
    expect((await readAppointment(appointmentId)).slot).toEqual([{ reference: `Slot/${newSlot}` }]);
    expect((await readSlot(oldSlot)).status).toBe("free");
    expect((await readSlot(newSlot)).status).toBe("busy");
  });

  test("makes a Cancel unknown with its Slot still busy, and the same Cancel sent again frees the Slot", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "12:30"));
    const appointmentId = (await book(slotId)).body.appointment.appointmentId;
    const idempotencyKey = randomUUID();
    const cancel = (fault?: string) =>
      adapter.post(`/appointments/${appointmentId}/cancel`, { patientId, idempotencyKey }, fault ? withFault(fault) : {});

    const halfDone = await cancel("half_write");

    expect(halfDone.body).toEqual({ outcome: "unknown" });
    expect((await readAppointment(appointmentId)).status).toBe("cancelled");
    expect((await readSlot(slotId)).status).toBe("busy");

    const retry = await cancel();

    expect(retry.body.outcome).toBe("succeeded");
    expect((await readSlot(slotId)).status).toBe("free");
  });
});

test("an unknown fault is an invalid request", async () => {
  const response = await adapter.get("/slots", {}, withFault("gremlins"));

  expect(response.status).toBe(400);
  expect(response.body).toEqual({ error: "invalid_request" });
});
