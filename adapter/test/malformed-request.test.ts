import { afterAll, beforeAll, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";

let adapter: Adapter;
beforeAll(async () => {
  adapter = await startAdapter();
});
afterAll(() => adapter.close());

test.each(["/patients/verify", "/appointments"])("a body that isn't JSON is an invalid request at %s", async (path) => {
  const response = await fetch(`${adapter.url}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{not json",
  });

  expect(response.status).toBe(400);
  expect(await response.json()).toEqual({ error: "invalid_request" });
});
