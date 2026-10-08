// Test-side access to the real HAPI: sets up data before a test and reads FHIR state after it.
// Tests never stub HAPI. Each test creates the records it needs so it runs the same on a seeded
// local stack and on the empty HAPI in CI. Every record a test file creates is deleted after it,
// because the local stack keeps its data and the seed tests count what the clinic holds.

import { randomUUID } from "node:crypto";
import type { Appointment, Bundle, FhirResource, Patient, Practitioner, Schedule, Slot, Task } from "fhir/r4";

export const fhirBaseUrl = (process.env.FHIR_BASE_URL ?? "http://localhost:8080/fhir").replace(/\/+$/, "");

const CLINIC_TIMEZONE = "America/New_York";
const PROVIDER_SYSTEM = "https://clinic.example/fhir/identifier/provider";
const IDEMPOTENCY_KEY_SYSTEM = "https://clinic.example/fhir/identifier/idempotency-key";
const VISIT_TYPE_SYSTEM = "https://clinic.example/fhir/CodeSystem/visit-type";

async function fhir<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${fhirBaseUrl}/${path}`, {
    method,
    headers: { accept: "application/fhir+json", "content-type": "application/fhir+json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${method} ${path} returned ${response.status}: ${await response.text()}`);
  return (await response.json()) as T;
}

async function search<T extends FhirResource>(query: string): Promise<T[]> {
  const bundle = await fhir<Bundle>("GET", query);
  return (bundle.entry ?? []).map((entry) => entry.resource as T);
}

const createdRecords: string[] = [];

async function create<T extends FhirResource>(resource: T): Promise<string> {
  const created = await fhir<T>("POST", resource.resourceType, resource);
  createdRecords.push(`${resource.resourceType}/${created.id}`);
  return created.id!;
}

// Newest first, so a record goes before the records it points at. Appointments the adapter booked
// into a test's Slots, and Callback Requests it filed for a test's Patients, aren't on the list, so
// they go first.
export async function deleteCreatedRecords() {
  for (const slot of createdRecords.filter((record) => record.startsWith("Slot/"))) {
    for (const appointment of await search<Appointment>(`Appointment?slot=${slot}`)) {
      await fhir("DELETE", `Appointment/${appointment.id}`);
    }
  }
  for (const patient of createdRecords.filter((record) => record.startsWith("Patient/"))) {
    for (const task of await search<Task>(`Task?patient=${patient}`)) {
      if (!createdRecords.includes(`Task/${task.id}`)) await fhir("DELETE", `Task/${task.id}`);
    }
  }
  while (createdRecords.length > 0) await fhir("DELETE", createdRecords.pop()!);
}

export async function createPatient(patient: { given: string[]; family: string; birthDate: string }): Promise<string> {
  return create<Patient>({
    resourceType: "Patient",
    active: true,
    name: [{ use: "official", family: patient.family, given: patient.given }],
    birthDate: patient.birthDate,
  });
}

// A date of birth no Patient has yet, so a test's own Patients are the only candidates for it.
// Dates come from the 1800s, which keeps clear of the seeded adults.
export async function unusedBirthDate(): Promise<string> {
  for (let attempt = 0; attempt < 20; attempt++) {
    const day = new Date(Date.UTC(1800, 0, 1) + Math.floor(Math.random() * 36_500) * 86_400_000);
    const birthDate = day.toISOString().slice(0, 10);
    const bundle = await fhir<Bundle>("GET", `Patient?birthdate=${birthDate}&_summary=count`);
    if (bundle.total === 0) return birthDate;
  }
  throw new Error("Could not find an unused date of birth");
}

// For records the adapter created. They are deleted along with the ones the test file made itself.
export function deleteAfterTests(resourceType: string, id: string) {
  createdRecords.push(`${resourceType}/${id}`);
}

export const readRecord = <T extends FhirResource>(resourceType: T["resourceType"], id: string) =>
  fhir<T>("GET", `${resourceType}/${id}`);

export type TestProvider = { providerId: string; scheduleId: string };

// A Provider of the test's own, with a key no other Provider has, so its Slots belong to the test alone.
export async function createProvider(name: { given: string; family: string }): Promise<TestProvider> {
  const providerId = `test-${randomUUID()}`;
  const practitionerId = await create<Practitioner>({
    resourceType: "Practitioner",
    identifier: [{ system: PROVIDER_SYSTEM, value: providerId }],
    active: true,
    name: [{ use: "official", family: name.family, given: [name.given], prefix: ["Dr."] }],
  });
  const scheduleId = await create<Schedule>({
    resourceType: "Schedule",
    active: true,
    actor: [{ reference: `Practitioner/${practitionerId}` }],
  });
  return { providerId, scheduleId };
}

// A 30-minute Slot for the Provider starting at `start`, an ISO 8601 time such as clinicTime() gives.
export async function createSlot(provider: TestProvider, start: string, status: Slot["status"] = "free"): Promise<string> {
  return create<Slot>({
    resourceType: "Slot",
    schedule: { reference: `Schedule/${provider.scheduleId}` },
    status,
    start,
    end: clinicIso(new Date(Date.parse(start) + 30 * 60_000)),
  });
}

// An Appointment written straight into HAPI, for states the adapter won't create, such as one in the
// past. It takes the Slot as it is. deleteCreatedRecords finds it through its Slot, so it isn't listed.
export async function createAppointment(
  patientId: string,
  slotId: string,
  status: Appointment["status"] = "booked",
): Promise<string> {
  const slot = await readSlot(slotId);
  const created = await fhir<Appointment>("POST", "Appointment", {
    resourceType: "Appointment",
    status,
    appointmentType: { coding: [{ system: VISIT_TYPE_SYSTEM, code: "follow_up" }], text: "Follow-up" },
    slot: [{ reference: `Slot/${slotId}` }],
    start: slot.start,
    end: slot.end,
    participant: [{ actor: { reference: `Patient/${patientId}` }, status: "accepted" }],
  } satisfies Appointment);
  return created.id!;
}

export const readSlot = (slotId: string) => fhir<Slot>("GET", `Slot/${slotId}`);

export const readAppointment = (appointmentId: string) => fhir<Appointment>("GET", `Appointment/${appointmentId}`);

export const appointmentsInSlot = (slotId: string) => search<Appointment>(`Appointment?slot=Slot/${slotId}`);

export const appointmentsWithKey = (idempotencyKey: string) =>
  search<Appointment>(`Appointment?identifier=${encodeURIComponent(`${IDEMPOTENCY_KEY_SYSTEM}|${idempotencyKey}`)}`);

// A wall-clock time at the clinic, `days` after today there, as ISO 8601 with the clinic's UTC offset.
export function clinicTime(days: number, time: string): string {
  const [year, month, day] = clinicToday().split("-").map(Number);
  const [hour, minute] = time.split(":").map(Number);
  const wall = Date.UTC(year!, month! - 1, day! + days, hour, minute);
  // The offset at the wall time itself can differ from the one at `wall` read as UTC, so look twice.
  const guess = wall - offsetMinutes(new Date(wall)) * 60_000;
  return clinicIso(new Date(wall - offsetMinutes(new Date(guess)) * 60_000));
}

// The clinic's calendar date `days` after today there, as YYYY-MM-DD.
export const clinicDate = (days: number) => clinicTime(days, "12:00").slice(0, 10);

const clinicToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: CLINIC_TIMEZONE }).format(new Date());

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
