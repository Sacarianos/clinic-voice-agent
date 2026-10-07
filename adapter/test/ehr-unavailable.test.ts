import { createServer } from "node:net";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";

// A port that was free a moment ago, so every FHIR call is refused.
async function closedPort(): Promise<number> {
  const server = createServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

let adapter: Adapter;
beforeAll(async () => {
  adapter = await startAdapter({ fhirBaseUrl: `http://127.0.0.1:${await closedPort()}/fhir` });
});
afterAll(() => adapter.close());

test("Verify patient answers ehr_unavailable when the EHR can't be reached", async () => {
  const response = await adapter.post("/patients/verify", {
    givenName: "Ada",
    familyName: "Byron",
    dateOfBirth: "1815-12-10",
  });

  expect(response.status).toBe(502);
  expect(response.body).toEqual({ error: "ehr_unavailable" });
});

test("Find slots answers ehr_unavailable when the EHR can't be reached", async () => {
  const response = await adapter.get("/slots");

  expect(response.status).toBe(502);
  expect(response.body).toEqual({ error: "ehr_unavailable" });
});

test("Book fails, so a retry with the same key is safe, when the EHR can't be reached", async () => {
  const response = await adapter.post("/appointments", {
    patientId: "p1",
    slotId: "s1",
    visitType: "sick_visit",
    idempotencyKey: "0a6d2c1e-5d1b-4b8e-9f5a-1c2d3e4f5a6b",
  });

  expect(response.status).toBe(200);
  expect(response.body).toEqual({ outcome: "failed" });
});
