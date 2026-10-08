import type { Appointment, Bundle, Slot } from "fhir/r4";
import { IDEMPOTENCY_KEY_SYSTEM } from "../clinic.ts";
import { EhrUnavailableError, type FhirClient } from "../fhir/client.ts";
import type { WriteOutcome } from "../write-outcome.ts";
import {
  appointmentDetails,
  slotIdOf,
  slotToTake,
  VISIT_TYPE_SYSTEM,
  VISIT_TYPES,
  type AppointmentDetails,
  type SlotRejection,
  type VisitType,
} from "./appointments.ts";
import { loadProviders, type Providers } from "./providers.ts";

export type BookRequest = {
  patientId: string;
  slotId: string;
  visitType: VisitType;
  // Chosen by the agent once per Read-back. A retry of the same Book sends the same key.
  idempotencyKey: string;
};

export type BookRejection = SlotRejection;

export type BookResult = WriteOutcome<{ appointment: AppointmentDetails }, BookRejection>;

// Creates the Appointment and marks its Slot busy in one FHIR transaction. HAPI doesn't check that a
// Slot is free, so the guard against double-booking is the Slot's version: the transaction only
// applies if nobody changed the Slot since it was read here.
export async function book(fhir: FhirClient, request: BookRequest, now: Date): Promise<BookResult> {
  try {
    // A retry after a commit must answer with the original Appointment. Retrying the transaction
    // would hit the Slot's new version and look like someone else took it.
    const earlier = await bookedWithKey(fhir, request.idempotencyKey);
    if (earlier) return earlier;

    const providers = await loadProviders(fhir);
    const taking = await slotToTake(fhir, request.slotId, providers, now);
    if ("rejection" in taking) return { outcome: "rejected", reason: taking.rejection };
    const { slot, provider } = taking;

    const appointment: Appointment = {
      resourceType: "Appointment",
      identifier: [{ system: IDEMPOTENCY_KEY_SYSTEM, value: request.idempotencyKey }],
      status: "booked",
      appointmentType: {
        coding: [{ system: VISIT_TYPE_SYSTEM, code: request.visitType, display: VISIT_TYPES[request.visitType] }],
        text: VISIT_TYPES[request.visitType],
      },
      slot: [{ reference: `Slot/${slot.id}` }],
      start: slot.start,
      end: slot.end,
      participant: [
        { actor: { reference: `Patient/${request.patientId}` }, status: "accepted" },
        { actor: { reference: `Practitioner/${provider.practitionerId}` }, status: "accepted" },
      ],
    };
    const transaction: Bundle<Slot | Appointment> = {
      resourceType: "Bundle",
      type: "transaction",
      entry: [
        {
          resource: { ...slot, status: "busy" },
          request: { method: "PUT", url: `Slot/${slot.id}`, ifMatch: `W/"${slot.meta?.versionId}"` },
        },
        {
          resource: appointment,
          // Second line of defence for a retry racing this one with the same key.
          request: {
            method: "POST",
            url: "Appointment",
            ifNoneExist: `identifier=${IDEMPOTENCY_KEY_SYSTEM}|${request.idempotencyKey}`,
          },
        },
      ],
    };

    const result = await fhir.transaction(transaction);
    if (result.status === "committed") {
      const appointmentId = result.response.entry?.[1]?.response?.location?.split("/")[1];
      return describe({ ...appointment, id: appointmentId }, slot, providers);
    }
    if (result.status === "conflict") {
      // The Slot changed under us. A racing retry with this same key may be the one that took it.
      return (await bookedWithKey(fhir, request.idempotencyKey)) ?? { outcome: "rejected", reason: "slot_taken" };
    }
    return { outcome: result.status };
  } catch (error) {
    // Only reads failed, so nothing was written and a retry with the same key is safe.
    if (error instanceof EhrUnavailableError) return { outcome: "failed" };
    throw error;
  }
}

async function bookedWithKey(fhir: FhirClient, idempotencyKey: string): Promise<BookResult | undefined> {
  const [appointment] = await fhir.search<Appointment>("Appointment", {
    identifier: `${IDEMPOTENCY_KEY_SYSTEM}|${idempotencyKey}`,
  });
  if (!appointment) return undefined;
  const slot = await fhir.read<Slot>("Slot", slotIdOf(appointment));
  if (!slot) throw new Error(`Appointment ${appointment.id} has no Slot`);
  return describe(appointment, slot, await loadProviders(fhir));
}

function describe(appointment: Appointment, slot: Slot, providers: Providers): BookResult {
  return { outcome: "succeeded", appointment: appointmentDetails(appointment, slot, providers) };
}
