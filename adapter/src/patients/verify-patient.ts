import type { Patient } from "fhir/r4";
import type { FhirClient } from "../fhir/client.ts";

export type VerifyPatientRequest = {
  givenName: string;
  familyName: string;
  dateOfBirth: string;
};

export type VerifyPatientResult = { status: "verified"; patientId: string } | { status: "not_verified" };

export async function verifyPatient(fhir: FhirClient, request: VerifyPatientRequest): Promise<VerifyPatientResult> {
  const patients = await fhir.search<Patient>("Patient", { birthdate: request.dateOfBirth });
  const matches = patients.filter((patient) =>
    (patient.name ?? []).some(
      (name) => name.family === request.familyName && name.given?.[0] === request.givenName,
    ),
  );
  if (matches.length === 1) return { status: "verified", patientId: matches[0]!.id! };
  return { status: "not_verified" };
}
