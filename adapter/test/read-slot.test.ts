import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import { clinicTime, createProvider, createSlot } from "./support/ehr.ts";

let adapter: Adapter;
beforeAll(async () => {
  adapter = await startAdapter();
});
afterAll(() => adapter.close());

describe("Read slot", () => {
  test("tells whether the Slot is free or busy, and who it is with", async () => {
    const provider = await createProvider({ given: "Imogen", family: "Faraday" });
    const free = await createSlot(provider, clinicTime(1, "09:00"));
    const busy = await createSlot(provider, clinicTime(1, "09:30"), "busy");

    const [freeResponse, busyResponse] = await Promise.all([adapter.get(`/slots/${free}`), adapter.get(`/slots/${busy}`)]);

    expect(freeResponse.status).toBe(200);
    expect(freeResponse.body).toEqual({
      slot: {
        slotId: free,
        providerId: provider.providerId,
        providerName: "Dr. Imogen Faraday",
        start: clinicTime(1, "09:00"),
        end: clinicTime(1, "09:30"),
        status: "free",
      },
    });
    expect(busyResponse.body.slot.status).toBe("busy");
  });

  test("a Slot that doesn't exist is not found", async () => {
    const response = await adapter.get(`/slots/${randomUUID()}`);

    expect(response.status).toBe(404);
    expect(response.body).toEqual({ error: "slot_not_found" });
  });
});
