import type { Appointment, Slot } from "fhir/r4";
import { bookingWindow, isInBookingWindow } from "../clinic.ts";
import type { FhirClient } from "../fhir/client.ts";
import { offeredSlot } from "./find-slots.ts";
import { loadProviders, type Provider, type Providers } from "./providers.ts";
import { isHeldFor } from "./slot-holds.ts";

export const VISIT_TYPES = {
  annual_physical: "Annual physical",
  sick_visit: "Sick visit",
  follow_up: "Follow-up",
} as const;

export type VisitType = keyof typeof VISIT_TYPES;

export const VISIT_TYPE_SYSTEM = "https://clinic.example/fhir/CodeSystem/visit-type";

// An Appointment as the agent sees it. Times are clinic wall-clock times with their UTC offset.
export type AppointmentDetails = {
  appointmentId: string;
  patientId: string;
  slotId: string;
  providerId: string;
  providerName: string;
  start: string;
  end: string;
  visitType: VisitType;
};

// The Patient's booked Appointments that haven't started yet, earliest first.
export async function listAppointments(fhir: FhirClient, patientId: string, now: Date): Promise<AppointmentDetails[]> {
  const resources = await fhir.search<Appointment | Slot>("Appointment", {
    patient: `Patient/${patientId}`,
    status: "booked",
    date: `ge${now.toISOString()}`,
    _sort: "date",
    _include: "Appointment:slot",
    _count: "50",
  });
  const slots = new Map(
    resources.filter((resource): resource is Slot => resource.resourceType === "Slot").map((slot) => [slot.id!, slot]),
  );
  const providers = await loadProviders(fhir);
  return resources
    .filter((resource): resource is Appointment => resource.resourceType === "Appointment")
    .filter((appointment) => new Date(appointment.start!) >= now)
    .map((appointment) => appointmentDetails(appointment, slots.get(slotIdOf(appointment))!, providers));
}

// The Patient's Appointment with this id. Undefined when there is none, or it is someone else's.
export async function readPatientsAppointment(
  fhir: FhirClient,
  patientId: string,
  appointmentId: string,
): Promise<Appointment | undefined> {
  const appointment = await fhir.read<Appointment>("Appointment", appointmentId);
  const isPatients = appointment?.participant.some(
    (participant) => participant.actor?.reference === `Patient/${patientId}`,
  );
  return isPatients ? appointment : undefined;
}

export function appointmentDetails(appointment: Appointment, slot: Slot, providers: Providers): AppointmentDetails {
  const provider = providers.bySchedule.get(scheduleIdOf(slot))!;
  const patient = appointment.participant.find((participant) => participant.actor?.reference?.startsWith("Patient/"));
  const { slotId, providerId, providerName, start, end } = offeredSlot(slot, provider);
  return {
    appointmentId: appointment.id!,
    patientId: patient!.actor!.reference!.split("/")[1]!,
    slotId,
    providerId,
    providerName,
    start,
    end,
    visitType: appointment.appointmentType!.coding![0]!.code as VisitType,
  };
}

export type SlotRejection = "slot_taken" | "slot_not_found" | "outside_booking_window";

// The Slot a write is about to take and its Provider, or the reason it can't be taken. A busy Slot
// is still the write's to take when this same write took it before and didn't finish.
export async function slotToTake(
  fhir: FhirClient,
  slotId: string,
  providers: Providers,
  now: Date,
  idempotencyKey: string,
): Promise<{ slot: Slot; provider: Provider } | { rejection: SlotRejection }> {
  const slot = await fhir.read<Slot>("Slot", slotId);
  const provider = slot && providers.bySchedule.get(scheduleIdOf(slot));
  if (!slot || !provider) return { rejection: "slot_not_found" };
  if (!isInBookingWindow(new Date(slot.start), bookingWindow(now))) return { rejection: "outside_booking_window" };
  if (slot.status !== "free" && !isHeldFor(slot, idempotencyKey)) return { rejection: "slot_taken" };
  return { slot, provider };
}

export const slotIdOf =(appointment: Appointment) => appointment.slot?.[0]?.reference?.split("/")[1] ?? "";

export const scheduleIdOf = (slot: Slot) => slot.schedule.reference?.split("/")[1] ?? "";
