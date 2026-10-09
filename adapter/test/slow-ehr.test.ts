// However slow the EHR, the adapter answers every request within its one deadline, so the agent
// always hears an outcome before it stops waiting.

import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import { startDelayedEhr, type DelayedEhr } from "./support/delayed-ehr.ts";
import { startHeldEhr, type HeldEhr } from "./support/held-ehr.ts";
import {
  appointmentsInSlot,
  clinicTime,
  createPatient,
  createProvider,
  createSlot,
  readSlot,
  unusedBirthDate,
  type TestProvider,
} from "./support/ehr.ts";

// Long enough for HAPI's own reads on a loaded machine, so only the slow EHR runs it out.
const DEADLINE_MS = 2_000;
// Time for the answer to reach the test once the adapter has it.
const ANSWER_SLACK_MS = 500;

test("the health check reports the deadline, so the agent can check it waits long enough", async () => {
  const adapter = await startAdapter({ requestDeadlineMs: 4_321 });
  try {
    expect((await adapter.get("/healthz")).body).toEqual({ ok: true, requestDeadlineMs: 4_321 });
  } finally {
    await adapter.close();
  }
});

let patientId: string;
let provider: TestProvider;
beforeAll(async () => {
  patientId = await createPatient({ given: ["Rosalind"], family: "Okonkwo", birthDate: await unusedBirthDate() });
  provider = await createProvider({ given: "Imogen", family: "Faraday" });
});

const book = (adapter: Adapter, slotId: string) =>
  adapter.post("/appointments", { patientId, slotId, visitType: "sick_visit", idempotencyKey: randomUUID() });

async function timed<T>(request: () => Promise<T>): Promise<{ answer: T; tookMs: number }> {
  const started = Date.now();
  const answer = await request();
  return { answer, tookMs: Date.now() - started };
}

describe("an EHR that takes a write and never answers", () => {
  let heldEhr: HeldEhr;
  let adapter: Adapter;
  beforeAll(async () => {
    heldEhr = await startHeldEhr();
    adapter = await startAdapter({ fhirBaseUrl: heldEhr.url, requestDeadlineMs: DEADLINE_MS });
  });
  afterAll(async () => {
    heldEhr.release();
    await adapter.close();
    await heldEhr.close();
  });

  test("Book answers unknown within the deadline", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "09:00"));

    const { answer, tookMs } = await timed(() => book(adapter, slotId));

    expect(answer.body).toEqual({ outcome: "unknown" });
    expect(tookMs).toBeLessThan(DEADLINE_MS + ANSWER_SLACK_MS);
    // The held write never reached HAPI.
    expect(await appointmentsInSlot(slotId)).toEqual([]);
    expect((await readSlot(slotId)).status).toBe("free");
  });
});

describe("an EHR that answers each request late", () => {
  // Each request alone is well within the deadline, but no two in a row are.
  const delayMs = DEADLINE_MS * 0.6;
  let delayedEhr: DelayedEhr;
  let adapter: Adapter;
  beforeAll(async () => {
    delayedEhr = await startDelayedEhr(delayMs);
    adapter = await startAdapter({ fhirBaseUrl: delayedEhr.url, requestDeadlineMs: DEADLINE_MS });
  });
  afterAll(async () => {
    await adapter.close();
    await delayedEhr.close();
  });

  test("Book answers failed within the deadline, having written nothing", async () => {
    const slotId = await createSlot(provider, clinicTime(1, "09:30"));

    const { answer, tookMs } = await timed(() => book(adapter, slotId));

    expect(answer.body).toEqual({ outcome: "failed" });
    expect(tookMs).toBeLessThan(DEADLINE_MS + ANSWER_SLACK_MS);
    expect(await appointmentsInSlot(slotId)).toEqual([]);
    expect((await readSlot(slotId)).status).toBe("free");
  });

  test("a search for Slots answers ehr_unavailable within the deadline", async () => {
    const { answer, tookMs } = await timed(() => adapter.get("/slots"));

    expect(answer.status).toBe(502);
    expect(answer.body).toEqual({ error: "ehr_unavailable" });
    expect(tookMs).toBeLessThan(DEADLINE_MS + ANSWER_SLACK_MS);
  });
});
