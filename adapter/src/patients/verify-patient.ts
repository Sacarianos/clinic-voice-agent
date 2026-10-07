import type { Patient } from "fhir/r4";
import type { FhirClient } from "../fhir/client.ts";
import { normalizeName, soundsAlike } from "./names.ts";

export type VerifyPatientRequest = {
  givenName: string;
  familyName: string;
  dateOfBirth: string;
};

export type VerifyPatientResult = { status: "verified"; patientId: string } | { status: "not_verified" };

export async function verifyPatient(fhir: FhirClient, request: VerifyPatientRequest): Promise<VerifyPatientResult> {
  const patients = await fhir.search<Patient>("Patient", { birthdate: request.dateOfBirth });
  const statedInitial = normalizeName(request.givenName)[0];
  const matches = patients.filter((patient) =>
    (patient.name ?? []).some(
      (name) =>
        name.family !== undefined &&
        soundsAlike(request.familyName, name.family) &&
        normalizeName(name.given?.[0] ?? "")[0] === statedInitial,
    ),
  );
  if (matches.length === 1) return { status: "verified", patientId: matches[0]!.id! };
  return { status: "not_verified" };
}
