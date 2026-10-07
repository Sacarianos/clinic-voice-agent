import { Hono } from "hono";
import type { Config } from "./config.ts";
import { createFhirClient } from "./fhir/client.ts";
import { patientRoutes } from "./patients/routes.ts";

export function createApp(config: Config) {
  const fhir = createFhirClient({ baseUrl: config.fhirBaseUrl });

  return new Hono()
    .get("/healthz", (c) => c.json({ ok: true }))
    .route("/patients", patientRoutes(fhir));
}
