// The adapter's own logs never hold patient data, even when a request carrying it fails.

import { randomUUID } from "node:crypto";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import { clinicTime, createPatient, createProvider, createSlot, unusedBirthDate } from "./support/ehr.ts";

const given = "Rosalind";
const family = "Okonkwo";
const phone = "+15558675309";
let birthDate: string;
let patientId: string;

let adapter: Adapter;
let unreachable: Adapter;
const logged: string[] = [];

beforeAll(async () => {
  birthDate = await unusedBirthDate();
  patientId = await createPatient({ given: [given], family, birthDate });
  adapter = await startAdapter({ fhirTimeoutMs: 500 });
  unreachable = await startAdapter({ fhirBaseUrl: "http://127.0.0.1:9/fhir" });
  for (const level of ["log", "info", "warn", "error", "debug", "trace"] as const) {
    vi.spyOn(console, level).mockImplementation((...args: unknown[]) => {
      logged.push(args.map(String).join(" "));
    });
  }
});
afterEach(() => {
  const output = logged.join("\n").toLowerCase();
  logged.length = 0;
  for (const phi of [given, family, birthDate, phone, phone.slice(2)]) {
    expect(output).not.toContain(phi.toLowerCase());
  }
});
afterAll(async () => {
  vi.restoreAllMocks();
  await Promise.all([adapter.close(), unreachable.close()]);
});

const verification = () => ({ givenName: given, familyName: family, dateOfBirth: birthDate });
const callbackRequest = () => ({ phoneNumber: phone, reason: "Caller asked to speak to a person", emergency: false, patientId });

test.each(["server_error", "timeout", "half_write"])("verifying a Patient while the EHR fails with %s", async (fault) => {
  await adapter.post("/patients/verify", verification(), { "x-inject-fault": fault });
});

test("verifying a Patient while the EHR can't be reached", async () => {
  const response = await unreachable.post("/patients/verify", verification());

  expect(response.status).toBe(502);
  expect(logged.join("\n")).toContain("POST /patients/verify");
});

test.each(["server_error", "timeout"])("filing a Callback Request while the EHR fails with %s", async (fault) => {
  await adapter.post("/callback-requests", callbackRequest(), { "x-inject-fault": fault });
});

test("filing a Callback Request while the EHR can't be reached", async () => {
  await unreachable.post("/callback-requests", callbackRequest());
});

test.each(["server_error", "timeout", "half_write", "slot_taken"])("booking while the EHR fails with %s", async (fault) => {
  const provider = await createProvider({ given: "Imogen", family: "Faraday" });
  const slotId = await createSlot(provider, clinicTime(1, "09:00"));

  await adapter.post(
    "/appointments",
    { patientId, slotId, visitType: "sick_visit", idempotencyKey: randomUUID() },
    { "x-inject-fault": fault },
  );
});

test.each(["/patients/verify", "/callback-requests"])("a broken or invalid body sent to %s", async (path) => {
  const broken = JSON.stringify({ ...verification(), ...callbackRequest() }).slice(0, -3);
  await fetch(`${adapter.url}${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: broken });
  await adapter.post(path, { ...verification(), dateOfBirth: `${given} ${birthDate}`, phoneNumber: 5558675309 });
});
