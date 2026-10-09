import type { Slot } from "fhir/r4";
import { addDays, bookingWindow, clinicHour, clinicIso, startOfClinicDay } from "../clinic.ts";
import type { FhirClient } from "../fhir/client.ts";
import { loadProviders, scheduleIdOf, type Provider } from "./providers.ts";

export type PartOfDay = "morning" | "afternoon";

export type FindSlotsRequest = {
  providerId?: string;
  // Clinic calendar dates, YYYY-MM-DD, both inclusive.
  from?: string;
  to?: string;
  partOfDay?: PartOfDay;
  limit: number;
};

export type OfferedSlot = {
  slotId: string;
  providerId: string;
  providerName: string;
  start: string;
  end: string;
};

export type FindSlotsResult =
  | { status: "found"; slots: OfferedSlot[]; bookingWindowLastDay: string }
  | { status: "unknown_provider" };

const NOON = 12;

// Free Slots inside the Booking Window that match every filter given, earliest first.
export async function findSlots(fhir: FhirClient, request: FindSlotsRequest, now: Date): Promise<FindSlotsResult> {
  const window = bookingWindow(now);
  const providers = await loadProviders(fhir);
  const schedules = request.providerId ? providers.schedulesOf(request.providerId) : undefined;
  if (schedules?.length === 0) return { status: "unknown_provider" };

  const from = latest(window.start, request.from ? startOfClinicDay(request.from) : undefined);
  const until = earliest(window.end, request.to ? startOfClinicDay(addDays(request.to, 1)) : undefined);
  const slots: OfferedSlot[] = [];
  if (from < until) {
    const params: [string, string][] = [
      ["status", "free"],
      ["start", `ge${from.toISOString()}`],
      ["start", `lt${until.toISOString()}`],
      ["_sort", "start"],
      ["_count", "100"],
    ];
    if (schedules) params.push(["schedule", schedules.map((id) => `Schedule/${id}`).join(",")]);

    for await (const slot of fhir.searchEach<Slot>("Slot", params)) {
      const provider = providers.bySchedule.get(scheduleIdOf(slot));
      if (!provider || !isInPartOfDay(new Date(slot.start), request.partOfDay)) continue;
      slots.push(offeredSlot(slot, provider));
      if (slots.length === request.limit) break;
    }
  }
  return { status: "found", slots, bookingWindowLastDay: window.lastDay };
}

export type SlotState = OfferedSlot & { status: "free" | "busy" };

// One Slot as it stands now, for checking what a write did. Undefined when it isn't a Provider's Slot.
export async function readSlot(fhir: FhirClient, slotId: string): Promise<SlotState | undefined> {
  const slot = await fhir.read<Slot>("Slot", slotId);
  const provider = slot && (await loadProviders(fhir)).bySchedule.get(scheduleIdOf(slot));
  if (!slot || !provider) return undefined;
  return { ...offeredSlot(slot, provider), status: slot.status === "free" ? "free" : "busy" };
}

export function offeredSlot(slot: Slot, provider: Provider): OfferedSlot {
  return {
    slotId: slot.id!,
    providerId: provider.providerId,
    providerName: provider.providerName,
    start: clinicIso(new Date(slot.start)),
    end: clinicIso(new Date(slot.end)),
  };
}

function isInPartOfDay(start: Date, partOfDay: PartOfDay | undefined): boolean {
  if (partOfDay === "morning") return clinicHour(start) < NOON;
  if (partOfDay === "afternoon") return clinicHour(start) >= NOON;
  return true;
}

const latest = (a: Date, b: Date | undefined) => (b && b > a ? b : a);
const earliest = (a: Date, b: Date | undefined) => (b && b < a ? b : a);
