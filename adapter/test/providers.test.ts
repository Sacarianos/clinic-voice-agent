import { afterAll, beforeAll, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import { createProvider } from "./support/ehr.ts";

let adapter: Adapter;
beforeAll(async () => {
  adapter = await startAdapter();
});
afterAll(() => adapter.close());

test("List providers names every Provider a Caller can book with", async () => {
  const faraday = await createProvider({ given: "Imogen", family: "Faraday" });
  const okafor = await createProvider({ given: "Chidi", family: "Okafor" });

  const response = await adapter.get("/providers");

  expect(response.status).toBe(200);
  expect(response.body.providers).toEqual(
    expect.arrayContaining([
      { providerId: faraday.providerId, providerName: "Dr. Imogen Faraday" },
      { providerId: okafor.providerId, providerName: "Dr. Chidi Okafor" },
    ]),
  );
});
