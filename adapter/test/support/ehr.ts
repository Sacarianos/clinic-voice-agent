// Test-side access to the real HAPI: sets up data before a test and reads FHIR state after it.
// Tests never stub HAPI. Each test creates the records it needs so it runs the same on a seeded
// local stack and on the empty HAPI in CI.

import type { Bundle, Patient } from "fhir/r4";

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

export async function createPatient(patient: { given: string[]; family: string; birthDate: string }): Promise<string> {
  const created = await fhir<Patient>("POST", "Patient", {
    resourceType: "Patient",
    active: true,
    name: [{ use: "official", family: patient.family, given: patient.given }],
    birthDate: patient.birthDate,
  } satisfies Patient);
  return created.id!;
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
