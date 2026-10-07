import type { Appointment, Bundle, Slot } from "fhir/r4";
import type { FhirClient } from "../fhir/client.ts";
import { versionGuardedWrite, type WriteOutcome } from "../write-outcome.ts";
import { appointmentDetails, readPatientsAppointment, slotIdOf, type AppointmentDetails } from "./appointments.ts";
import { loadProviders } from "./providers.ts";

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

    if (appointment.status === "cancelled") return succeeded();
    if (new Date(appointment.start!) < now) return { outcome: "rejected", reason: "appointment_in_past" };

    const transaction: Bundle<Slot | Appointment> = {
      resourceType: "Bundle",
      type: "transaction",
      entry: [
        {
          resource: cancelled,
          request: { method: "PUT", url: `Appointment/${appointment.id}`, ifMatch: `W/"${appointment.meta?.versionId}"` },
        },
        {
          resource: { ...slot, status: "free" },
          request: { method: "PUT", url: `Slot/${slot.id}`, ifMatch: `W/"${slot.meta?.versionId}"` },
        },
      ],
    };
    const result = await fhir.transaction(transaction);
    if (result.status === "committed") return succeeded();
    if (result.status === "conflict") return "conflict";
    return { outcome: result.status };
  });
}
