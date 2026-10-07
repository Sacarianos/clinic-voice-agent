// Facts about the fictional clinic, and its calendar. The clinic keeps time in one zone, so every
// date a Caller says and every time the agent reads back is a wall-clock time there.

export const CLINIC_TIMEZONE = "America/New_York";

// A Caller can book from now until the end of the day two weeks from today, clinic time.
export const BOOKING_WINDOW_DAYS = 14;

const IDENTIFIER_BASE = "https://clinic.example/fhir/identifier";
export const PROVIDER_SYSTEM = `${IDENTIFIER_BASE}/provider`;
export const IDEMPOTENCY_KEY_SYSTEM = `${IDENTIFIER_BASE}/idempotency-key`;

export type BookingWindow = { start: Date; end: Date; lastDay: string };

export function bookingWindow(now: Date): BookingWindow {
  const lastDay = addDays(clinicDate(now), BOOKING_WINDOW_DAYS);
  return { start: now, end: startOfClinicDay(addDays(lastDay, 1)), lastDay };
}

export const isInBookingWindow = (start: Date, window: BookingWindow) => start >= window.start && start < window.end;

// The calendar date at the clinic for an instant, as YYYY-MM-DD.
export const clinicDate = (instant: Date) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: CLINIC_TIMEZONE }).format(instant);

export const clinicHour = (instant: Date) => Number(clinicIso(instant).slice(11, 13));

export function addDays(date: string, days: number): string {
  const [year, month, day] = date.split("-").map(Number);
  return new Date(Date.UTC(year!, month! - 1, day! + days)).toISOString().slice(0, 10);
}

// Midnight at the clinic on a YYYY-MM-DD date.
export function startOfClinicDay(date: string): Date {
  const wall = Date.parse(`${date}T00:00:00Z`);
  // The offset at the wall time itself can differ from the one at `wall` read as UTC, so look twice.
  const guess = wall - offsetMinutes(new Date(wall)) * 60_000;
  return new Date(wall - offsetMinutes(new Date(guess)) * 60_000);
}

// An instant as ISO 8601 in clinic wall-clock time with its UTC offset, such as 2026-10-08T09:00:00-04:00.
export function clinicIso(instant: Date): string {
  const offset = offsetMinutes(instant);
  const wall = new Date(instant.getTime() + offset * 60_000).toISOString().slice(0, 19);
  const hours = String(Math.floor(Math.abs(offset) / 60)).padStart(2, "0");
  const minutes = String(Math.abs(offset) % 60).padStart(2, "0");
  return `${wall}${offset < 0 ? "-" : "+"}${hours}:${minutes}`;
}

function offsetMinutes(instant: Date): number {
  const name = new Intl.DateTimeFormat("en-US", { timeZone: CLINIC_TIMEZONE, timeZoneName: "longOffset" })
    .formatToParts(instant)
    .find((part) => part.type === "timeZoneName")!.value;
  const match = /GMT([+-])(\d{2}):(\d{2})/.exec(name);
  if (!match) return 0;
  return (match[1] === "-" ? -1 : 1) * (Number(match[2]) * 60 + Number(match[3]));
}
