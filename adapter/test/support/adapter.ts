// Runs the real adapter on a free port and talks to it over HTTP, the way the agent does.

import { serve } from "@hono/node-server";
import type { AddressInfo } from "node:net";
import { createApp } from "../../src/app.ts";
import { loadConfig, type Config } from "../../src/config.ts";
import { fhirBaseUrl } from "./ehr.ts";

export type Adapter = {
  url: string;
  post(path: string, body: unknown, headers?: Record<string, string>): Promise<{ status: number; body: any }>;
  close(): Promise<void>;
};

export async function startAdapter(overrides: Partial<Config> = {}): Promise<Adapter> {
  const config = { ...loadConfig({ FHIR_BASE_URL: fhirBaseUrl }), ...overrides };
  const app = createApp(config);
  const server = await new Promise<ReturnType<typeof serve>>((resolve) => {
    const started = serve({ fetch: app.fetch, port: 0, hostname: "127.0.0.1" }, () => resolve(started));
  });
  const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;

  return {
    url,
    async post(path, body, headers = {}) {
      const response = await fetch(`${url}${path}`, {
        method: "POST",
        headers: { "content-type": "application/json", ...headers },
        body: JSON.stringify(body),
      });
      return { status: response.status, body: await response.json() };
    },
    close: () => new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
  };
}
