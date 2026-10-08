import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Config } from "./config.ts";
import { createFhirClient, EhrUnavailableError } from "./fhir/client.ts";
import { callbackRequestRoutes } from "./callback-requests/routes.ts";
import { patientRoutes } from "./patients/routes.ts";
import { appointmentRoutes, providerRoutes, slotRoutes } from "./scheduling/routes.ts";

// Error bodies are always { error: <code> }. Domain results, including a failed verification and every
// write outcome (see write-outcome.ts), are 200s.
export function createApp(config: Config) {
  const fhir = createFhirClient({ baseUrl: config.fhirBaseUrl });

  return new Hono()
    .get("/healthz", (c) => c.json({ ok: true }))
    .route("/patients", patientRoutes(fhir))
    .route("/callback-requests", callbackRequestRoutes(fhir))
    .route("/providers", providerRoutes(fhir))
    .route("/slots", slotRoutes(fhir))
    .route("/appointments", appointmentRoutes(fhir))
    .notFound((c) => c.json({ error: "not_found" }, 404))
    .onError((error, c) => {
      // Only the error's own message is logged. It never includes request bodies.
      console.error(`${c.req.method} ${c.req.path}: ${error.name}: ${error.message}`);
      if (error instanceof EhrUnavailableError) return c.json({ error: "ehr_unavailable" }, 502);
      // Hono's validators throw a 400 for a body that isn't JSON at all.
      if (error instanceof HTTPException && error.status === 400) return c.json({ error: "invalid_request" }, 400);
      return c.json({ error: "internal_error" }, 500);
    });
}
