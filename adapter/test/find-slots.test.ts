import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import { clinicDate, clinicIso, clinicTime, createProvider, createSlot } from "./support/ehr.ts";

let adapter: Adapter;
beforeAll(async () => {
  adapter = await startAdapter();
});
afterAll(() => adapter.close());

const findSlots = (query: Record<string, string>) => adapter.get("/slots", query);

describe("Find slots", () => {
  test("for a Provider, the free Slots come back earliest first with who they are with", async () => {
    const provider = await createProvider({ given: "Imogen", family: "Faraday" });
    const later = await createSlot(provider, clinicTime(2, "09:00"));
    const earlier = await createSlot(provider, clinicTime(1, "14:30"));
    await createSlot(provider, clinicTime(1, "10:00"), "busy");

    const response = await findSlots({ providerId: provider.providerId });

    expect(response.status).toBe(200);
    expect(response.body.slots).toEqual([
      {
        slotId: earlier,
        providerId: provider.providerId,
        providerName: "Dr. Imogen Faraday",
        start: clinicTime(1, "14:30"),
        end: clinicTime(1, "15:00"),
      },
      {
        slotId: later,
        providerId: provider.providerId,
        providerName: "Dr. Imogen Faraday",
        start: clinicTime(2, "09:00"),
        end: clinicTime(2, "09:30"),
      },
    ]);
  });

  test("a date range keeps only Slots on those clinic days, both ends included", async () => {
    const provider = await createProvider({ given: "Imogen", family: "Faraday" });
    await createSlot(provider, clinicTime(2, "16:30"));
    const firstDay = await createSlot(provider, clinicTime(3, "08:00"));
    const lastDay = await createSlot(provider, clinicTime(4, "16:30"));
    await createSlot(provider, clinicTime(5, "08:00"));

    const response = await findSlots({ providerId: provider.providerId, from: clinicDate(3), to: clinicDate(4) });

    expect(slotIds(response)).toEqual([firstDay, lastDay]);
  });

  test.each([
    ["morning", ["08:00", "11:30"]],
    ["afternoon", ["12:00", "16:30"]],
  ])("asking for the %s keeps only Slots that start then", async (partOfDay, expected) => {
    const provider = await createProvider({ given: "Imogen", family: "Faraday" });
    const slots = new Map<string, string>();
    for (const time of ["08:00", "11:30", "12:00", "16:30"]) slots.set(time, await createSlot(provider, clinicTime(1, time)));

    const response = await findSlots({ providerId: provider.providerId, partOfDay, limit: "10" });

    expect(slotIds(response)).toEqual(expected.map((time) => slots.get(time)));
  });

  test("Slots that already started or start after the Booking Window are never offered", async () => {
    const provider = await createProvider({ given: "Imogen", family: "Faraday" });
    await createSlot(provider, clinicIso(new Date(Date.now() - 5 * 60_000)));
    const soon = await createSlot(provider, clinicIso(new Date(Date.now() + 5 * 60_000)));
    const lastOne = await createSlot(provider, clinicTime(14, "23:30"));
    await createSlot(provider, clinicTime(15, "00:00"));

    const response = await findSlots({ providerId: provider.providerId, limit: "10" });

    expect(slotIds(response)).toEqual([soon, lastOne]);
    expect(response.body.bookingWindowLastDay).toBe(clinicDate(14));
  });

  test("a date range past the Booking Window finds nothing and says how far out booking goes", async () => {
    const provider = await createProvider({ given: "Imogen", family: "Faraday" });
    await createSlot(provider, clinicTime(20, "09:00"));

    const response = await findSlots({ providerId: provider.providerId, from: clinicDate(20), to: clinicDate(20) });

    expect(response.status).toBe(200);
    expect(response.body).toEqual({ slots: [], bookingWindowLastDay: clinicDate(14) });
  });

  test("without a Provider, Slots with any Provider are offered", async () => {
    const faraday = await createProvider({ given: "Imogen", family: "Faraday" });
    const okafor = await createProvider({ given: "Chidi", family: "Okafor" });
    // Off the half hour, so seeded Slots and other tests' Slots don't start at the same moment.
    const start = clinicTime(1, `07:${String(1 + Math.floor(Math.random() * 28)).padStart(2, "0")}`);
    const withFaraday = await createSlot(faraday, start);
    const withOkafor = await createSlot(okafor, start);

    const response = await findSlots({ from: clinicDate(1), to: clinicDate(1), limit: "20" });

    const atThatTime = response.body.slots.filter((slot: { start: string }) => slot.start === start);
    expect(atThatTime.map((slot: { slotId: string }) => slot.slotId).sort()).toEqual([withFaraday, withOkafor].sort());
    expect(atThatTime.map((slot: { providerName: string }) => slot.providerName).sort()).toEqual([
      "Dr. Chidi Okafor",
      "Dr. Imogen Faraday",
    ]);
  });

  test("offers three Slots unless asked for another number", async () => {
    const provider = await createProvider({ given: "Imogen", family: "Faraday" });
    for (const time of ["08:00", "08:30", "09:00", "09:30"]) await createSlot(provider, clinicTime(1, time));

    const three = await findSlots({ providerId: provider.providerId });
    const two = await findSlots({ providerId: provider.providerId, limit: "2" });

    expect(three.body.slots).toHaveLength(3);
    expect(two.body.slots).toHaveLength(2);
  });

  test("a Provider the clinic doesn't have is an error", async () => {
    const response = await findSlots({ providerId: "nobody" });

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: "unknown_provider" });
  });

  test.each([
    ["a date that isn't a date", { from: "2026-02-30" }],
    ["an unknown part of day", { partOfDay: "evening" }],
    ["a limit of zero", { limit: "0" }],
  ])("%s is an invalid request", async (_, query) => {
    const response = await findSlots(query);

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: "invalid_request" });
  });
});

const slotIds = (response: { body: { slots: { slotId: string }[] } }) => response.body.slots.map((slot) => slot.slotId);
