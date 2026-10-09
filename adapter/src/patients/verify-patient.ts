import type { HumanName, Patient } from "fhir/r4";
import type { FhirClient } from "../fhir/client.ts";
import { normalizeName, surnamesMatch } from "./names.ts";

export type VerifyPatientRequest = {
  givenName: string;
  familyName: string;
  dateOfBirth: string;
  // Set by the agent on the attempt that follows its spelling request, never by what the Caller claims.
  familyNameSpelled?: boolean;
};

// Never says which part failed, so the phone line can't be used to learn who is a Patient here.
export type VerifyPatientResult =
  | { status: "verified"; patientId: string }
  | { status: "ambiguous" }
  | { status: "not_verified" };

export async function verifyPatient(fhir: FhirClient, request: VerifyPatientRequest): Promise<VerifyPatientResult> {
  const patients = await fhir.search<Patient>("Patient", { birthdate: request.dateOfBirth, _count: "100" });
  // Surname matching is loose: Smith and Smyth match, and so do Brennan and Brendan. So every Patient it finds
  // counts. Speech recognition can write down one Patient's exact name for the other.
  let candidates = patients.filter((patient) => matchingNames(patient, request).length > 0);
  if (request.familyNameSpelled) {
    // Letters spelled one by one are what the Caller meant, so an exact surname may now break the tie.
    const spelled = normalizeName(request.familyName);
    const exact = candidates.filter((patient) =>
      matchingNames(patient, request).some((name) => normalizeName(name.family ?? "") === spelled),
    );
    if (exact.length > 0) candidates = exact;
  }

  if (candidates.length === 0) return { status: "not_verified" };
  if (candidates.length > 1) return { status: "ambiguous" };
  return { status: "verified", patientId: candidates[0]!.id! };
}

function matchingNames(patient: Patient, request: VerifyPatientRequest): HumanName[] {
  const statedGiven = normalizeName(request.givenName);
  return (patient.name ?? []).filter((name) => {
    const given = normalizeName(name.given?.[0] ?? "");
    return given !== "" && given[0] === statedGiven[0] && surnamesMatch(request.familyName, name.family ?? "");
  });
}
