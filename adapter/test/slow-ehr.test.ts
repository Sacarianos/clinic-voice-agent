import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import { startHeldEhr, type HeldEhr } from "./support/held-ehr.ts";
import { appointmentsInSlot, clinicTime, createPatient, createProvider, createSlot, readSlot, unusedBirthDate } from "./support/ehr.ts";

// An EHR that takes a write and never answers. The adapter must give up on it rather than hang.
let heldEhr: HeldEhr;
let adapter: Adapter;
beforeAll(async () => {
  heldEhr = await startHeldEhr();
  adapter = await startAdapter({ fhirBaseUrl: heldEhr.url, fhirTimeoutMs: 300 });
});
afterAll(async () => {
  heldEhr.release();
  await adapter.close();
  await heldEhr.close();
});

test("Book answers unknown, within the FHIR timeout, when the EHR doesn't answer the write", async () => {
  const patientId = await createPatient({ given: ["Rosalind"], family: "Okonkwo", birthDate: await unusedBirthDate() });
  const provider = await createProvider({ given: "Imogen", family: "Faraday" });
  const slotId = await createSlot(provider, clinicTime(1, "09:00"));
  const started = Date.now();

  const response = await adapter.post("/appointments", {
    patientId,
    slotId,
    visitType: "sick_visit",
    idempotencyKey: randomUUID(),
  });

  expect(response.body).toEqual({ outcome: "unknown" });
  expect(Date.now() - started).toBeLessThan(2_000);
  // The held write never reached HAPI.
  expect(await appointmentsInSlot(slotId)).toEqual([]);
  expect((await readSlot(slotId)).status).toBe("free");
});
