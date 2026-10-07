import { Hono } from "hono";
import { z } from "zod";
import type { FhirClient } from "../fhir/client.ts";
import { validJson } from "../http.ts";
import { createCallbackRequest } from "./create-callback-request.ts";

const createCallbackRequestBody = z.object({
  phoneNumber: z.string().trim().min(1),
  reason: z.string().trim().min(1),
  emergency: z.boolean(),
  patientId: z.string().trim().min(1).optional(),
});

export function callbackRequestRoutes(fhir: FhirClient) {
  return new Hono().post("/", validJson(createCallbackRequestBody), async (c) =>
    c.json(await createCallbackRequest(fhir, c.req.valid("json")), 201),
  );
}
