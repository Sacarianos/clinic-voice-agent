import type { Appointment, Slot } from "fhir/r4";
import type { FhirClient } from "../fhir/client.ts";
import { commitGuarded, guardedPut, versionGuardedWrite, type WriteOutcome } from "../write-outcome.ts";
import {
  appointmentDetails,
  readPatientsAppointment,
  slotIdOf,
  slotToTake,
  type AppointmentDetails,
  type SlotRejection,
} from "./appointments.ts";
import { loadProviders } from "./providers.ts";
import { freed, taken } from "./slot-holds.ts";

export type RescheduleRequest = {
  patientId: string;
  appointmentId: string;
  slotId: string;
  // Chosen by the agent once per Read-back. Retries are recognised by the target state instead: an
  // Appointment that already holds the new Slot is a success.
  idempotencyKey: string;
};

export type RescheduleRejection =
  | SlotRejection
  | "appointment_not_found"
  | "appointment_cancelled"
  | "appointment_in_past";

export type RescheduleResult = WriteOutcome<{ appointment: AppointmentDetails }, RescheduleRejection>;

// Moves the Appointment to the new Slot, frees the old Slot and takes the new one in one FHIR
// transaction. Every entry is guarded by the version read here, so a Book or another Reschedule that
// takes the new Slot first makes this one conflict instead of double-booking it.
export function reschedule(fhir: FhirClient, request: RescheduleRequest, now: Date): Promise<RescheduleResult> {
  return versionGuardedWrite<RescheduleResult>(async () => {
    const appointment = await readPatientsAppointment(fhir, request.patientId, request.appointmentId);
    if (!appointment) return { outcome: "rejected", reason: "appointment_not_found" };
    const providers = await loadProviders(fhir);
    const oldSlot = await fhir.read<Slot>("Slot", slotIdOf(appointment));
    if (!oldSlot) throw new Error(`Appointment ${appointment.id} has no Slot`);

    if (appointment.status === "booked" && oldSlot.id === request.slotId) {
      return { outcome: "succeeded", appointment: appointmentDetails(appointment, oldSlot, providers) };
    }
    if (appointment.status === "cancelled") return { outcome: "rejected", reason: "appointment_cancelled" };
    if (new Date(appointment.start!) < now) return { outcome: "rejected", reason: "appointment_in_past" };
    const taking = await slotToTake(fhir, request.slotId, providers, now, request.idempotencyKey);
    if ("rejection" in taking) return { outcome: "rejected", reason: taking.rejection };
    const { slot: newSlot, provider } = taking;

    const moved: Appointment = {
      ...appointment,
      slot: [{ reference: `Slot/${newSlot.id}` }],
      start: newSlot.start,
      end: newSlot.end,
      participant: [
        ...appointment.participant.filter((participant) => !participant.actor?.reference?.startsWith("Practitioner/")),
        { actor: { reference: `Practitioner/${provider.practitionerId}` }, status: "accepted" },
      ],
    };
    // Taking the new Slot comes first, so a write that lands only in part never leaves the
    // Appointment in a Slot someone else could still book.
    return commitGuarded(
      fhir,
      [guardedPut(taken(newSlot, request.idempotencyKey)), guardedPut(moved), guardedPut(freed(oldSlot))],
      () => ({ outcome: "succeeded" as const, appointment: appointmentDetails(moved, newSlot, providers) }),
    );
  });
}
