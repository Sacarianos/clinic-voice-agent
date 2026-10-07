import { Hono } from "hono";
import { z } from "zod";
import type { FhirClient } from "../fhir/client.ts";
import { validJson, validQuery } from "../http.ts";
import { book, VISIT_TYPES, type VisitType } from "./book.ts";
import { findSlots } from "./find-slots.ts";

const findSlotsQuery = z.object({
  providerId: z.string().min(1).optional(),
  from: z.iso.date().optional(),
  to: z.iso.date().optional(),
  partOfDay: z.enum(["morning", "afternoon"]).optional(),
  limit: z.coerce.number().int().min(1).max(20).default(3),
});

export function slotRoutes(fhir: FhirClient) {
  return new Hono().get("/", validQuery(findSlotsQuery), async (c) => {
    const result = await findSlots(fhir, c.req.valid("query"), new Date());
    if (result.status === "unknown_provider") return c.json({ error: "unknown_provider" }, 400);
    return c.json({ slots: result.slots, bookingWindowLastDay: result.bookingWindowLastDay });
  });
}

const bookBody = z.object({
  patientId: z.string().min(1),
  slotId: z.string().min(1),
  visitType: z.enum(Object.keys(VISIT_TYPES) as [VisitType, ...VisitType[]]),
  idempotencyKey: z.string().min(8).max(64),
});

export function appointmentRoutes(fhir: FhirClient) {
  return new Hono().post("/", validJson(bookBody), async (c) => c.json(await book(fhir, c.req.valid("json"), new Date())));
}
