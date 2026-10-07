import { serve } from "@hono/node-server";
import { createApp } from "./app.ts";
import { loadConfig } from "./config.ts";

const config = loadConfig();
const server = serve({ fetch: createApp(config).fetch, port: config.port }, ({ port }) => {
  console.log(`EHR adapter listening on ${port}, EHR at ${config.fhirBaseUrl}`);
});

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
