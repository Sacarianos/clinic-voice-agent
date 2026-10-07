import { Hono } from "hono";
import { z } from "zod";
import type { FhirClient } from "../fhir/client.ts";
import { validJson } from "../http.ts";
import { verifyPatient } from "./verify-patient.ts";

const verifyPatientBody = z.object({
  givenName: z.string().trim().min(1),
  familyName: z.string().trim().min(1),
  dateOfBirth: z.iso.date(),
  familyNameSpelled: z.boolean().optional(),
});

export function patientRoutes(fhir: FhirClient) {
  return new Hono().post("/verify", validJson(verifyPatientBody), async (c) =>
    c.json(await verifyPatient(fhir, c.req.valid("json"))),
  );
}
