// Test-side access to the real HAPI: sets up data before a test and reads FHIR state after it.
// Tests never stub HAPI. Each test creates the records it needs so it runs the same on a seeded
// local stack and on the empty HAPI in CI. Every record a test file creates is deleted after it,
// because the local stack keeps its data and the seed tests count what the clinic holds.

import type { Bundle, FhirResource, Patient } from "fhir/r4";

export const fhirBaseUrl = (process.env.FHIR_BASE_URL ?? "http://localhost:8080/fhir").replace(/\/+$/, "");

async function fhir<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${fhirBaseUrl}/${path}`, {
    method,
    headers: { accept: "application/fhir+json", "content-type": "application/fhir+json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${method} ${path} returned ${response.status}: ${await response.text()}`);
  return (await response.json()) as T;
}

const createdRecords: string[] = [];

async function create<T extends FhirResource>(resource: T): Promise<string> {
  const created = await fhir<T>("POST", resource.resourceType, resource);
  createdRecords.push(`${resource.resourceType}/${created.id}`);
  return created.id!;
}

// Newest first, so a record goes before the records it points at.
export async function deleteCreatedRecords() {
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
