import { Hono } from "hono";
import { z } from "zod";
import type { FhirClient } from "../fhir/client.ts";
import { validJson, validQuery } from "../http.ts";
import { listAppointments, VISIT_TYPES, type VisitType } from "./appointments.ts";
import { book } from "./book.ts";
import { cancel } from "./cancel.ts";
import { findSlots, readSlot } from "./find-slots.ts";
import { loadProviders } from "./providers.ts";
import { releaseSlot } from "./release-slot.ts";
import { reschedule } from "./reschedule.ts";

export function providerRoutes(fhir: FhirClient) {
  return new Hono().get("/", async (c) => {
    const { all } = await loadProviders(fhir);
    return c.json({ providers: all.map(({ providerId, providerName }) => ({ providerId, providerName })) });
  });
}

const findSlotsQuery = z.object({
  providerId: z.string().min(1).optional(),
  from: z.iso.date().optional(),
  to: z.iso.date().optional(),
  partOfDay: z.enum(["morning", "afternoon"]).optional(),
  limit: z.coerce.number().int().min(1).max(20).default(3),
});

export function slotRoutes(fhir: FhirClient) {
  return new Hono()
    .get("/", validQuery(findSlotsQuery), async (c) => {
      const result = await findSlots(fhir, c.req.valid("query"), new Date());
      if (result.status === "unknown_provider") return c.json({ error: "unknown_provider" }, 400);
      return c.json({ slots: result.slots, bookingWindowLastDay: result.bookingWindowLastDay });
    })
    .get("/:slotId", async (c) => {
      const slot = await readSlot(fhir, c.req.param("slotId"));
      return slot ? c.json({ slot }) : c.json({ error: "slot_not_found" }, 404);
    })
    .post("/:slotId/release", validJson(releaseSlotBody), async (c) =>
      c.json(await releaseSlot(fhir, c.req.param("slotId"), c.req.valid("json").idempotencyKey)),
    );
}

const releaseSlotBody = z.object({ idempotencyKey: z.string().min(8).max(64) });

const bookBody = z.object({
  patientId: z.string().min(1),
  slotId: z.string().min(1),
  visitType: z.enum(Object.keys(VISIT_TYPES) as [VisitType, ...VisitType[]]),
  idempotencyKey: z.string().min(8).max(64),
});

const listAppointmentsQuery = z.object({ patientId: z.string().min(1) });

const rescheduleBody = z.object({
  patientId: z.string().min(1),
  slotId: z.string().min(1),
  idempotencyKey: z.string().min(8).max(64),
});

const cancelBody = z.object({
  patientId: z.string().min(1),
  idempotencyKey: z.string().min(8).max(64),
});

export function appointmentRoutes(fhir: FhirClient) {
  return new Hono()
    .get("/", validQuery(listAppointmentsQuery), async (c) =>
      c.json({ appointments: await listAppointments(fhir, c.req.valid("query").patientId, new Date()) }),
    )
    .post("/", validJson(bookBody), async (c) => c.json(await book(fhir, c.req.valid("json"), new Date())))
    .post("/:appointmentId/reschedule", validJson(rescheduleBody), async (c) =>
      c.json(await reschedule(fhir, { ...c.req.valid("json"), appointmentId: c.req.param("appointmentId") }, new Date())),
    )
    .post("/:appointmentId/cancel", validJson(cancelBody), async (c) =>
      c.json(await cancel(fhir, { ...c.req.valid("json"), appointmentId: c.req.param("appointmentId") }, new Date())),
    );
}
