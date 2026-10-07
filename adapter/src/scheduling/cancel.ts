import type { Appointment, Bundle, Slot } from "fhir/r4";
import type { FhirClient } from "../fhir/client.ts";
import { versionGuardedWrite, type WriteOutcome } from "../write-outcome.ts";
import { appointmentDetails, readPatientsAppointment, slotIdOf, type AppointmentDetails } from "./appointments.ts";
import { loadProviders } from "./providers.ts";
import { freed } from "./slot-holds.ts";

export type CancelRequest = {
  patientId: string;
  appointmentId: string;
  // Chosen by the agent once per Read-back. Retries are recognised by the target state instead: an
  // Appointment that is already cancelled is a success.
  idempotencyKey: string;
};

export type CancelRejection = "appointment_not_found" | "appointment_in_past";

export type CancelResult = WriteOutcome<{ appointment: AppointmentDetails }, CancelRejection>;

// Cancels the Appointment and frees its Slot in one FHIR transaction, guarded by both versions.
export function cancel(fhir: FhirClient, request: CancelRequest, now: Date): Promise<CancelResult> {
  return versionGuardedWrite<CancelResult>(async () => {
    const appointment = await readPatientsAppointment(fhir, request.patientId, request.appointmentId);
    if (!appointment) return { outcome: "rejected", reason: "appointment_not_found" };
    const slot = await fhir.read<Slot>("Slot", slotIdOf(appointment));
    if (!slot) throw new Error(`Appointment ${appointment.id} has no Slot`);
    const providers = await loadProviders(fhir);
    const cancelled: Appointment = { ...appointment, status: "cancelled" };
    const succeeded = () => ({ outcome: "succeeded" as const, appointment: appointmentDetails(cancelled, slot, providers) });
    const freeSlot = {
      resource: freed(slot),
      request: { method: "PUT" as const, url: `Slot/${slot.id}`, ifMatch: `W/"${slot.meta?.versionId}"` },
    };

    if (appointment.status === "cancelled") {
      if (!isStillHeldFor(slot, appointment)) return succeeded();
      // An earlier Cancel landed only in part: the Appointment ended but its Slot stayed busy.
      const result = await fhir.transaction({ resourceType: "Bundle", type: "transaction", entry: [freeSlot] });
      if (result.status === "committed") return succeeded();
      if (result.status === "conflict") return "conflict";
      return { outcome: result.status };
    }
    if (new Date(appointment.start!) < now) return { outcome: "rejected", reason: "appointment_in_past" };

    // Ending the Appointment comes first, so a write that lands only in part never frees a Slot
    // that a booked Appointment still holds.
    const transaction: Bundle<Slot | Appointment> = {
      resourceType: "Bundle",
      type: "transaction",
      entry: [
        {
          resource: cancelled,
          request: { method: "PUT", url: `Appointment/${appointment.id}`, ifMatch: `W/"${appointment.meta?.versionId}"` },
        },
        freeSlot,
      ],
    };
    const result = await fhir.transaction(transaction);
    if (result.status === "committed") return succeeded();
    if (result.status === "conflict") return "conflict";
    return { outcome: result.status };
  });
}

// A cancelled Appointment's Slot that is busy and unchanged since the Appointment ended is still
// held for it. Once anyone else has written the Slot, it is theirs.
const isStillHeldFor = (slot: Slot, appointment: Appointment) =>
  slot.status === "busy" && Date.parse(slot.meta!.lastUpdated!) <= Date.parse(appointment.meta!.lastUpdated!);
