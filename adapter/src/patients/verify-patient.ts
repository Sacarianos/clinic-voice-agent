import type { HumanName, Patient } from "fhir/r4";
import type { FhirClient } from "../fhir/client.ts";
import { normalizeName, soundsAlike } from "./names.ts";

export type VerifyPatientRequest = {
  givenName: string;
  familyName: string;
  dateOfBirth: string;
};

// Never says which part failed, so the phone line can't be used to learn who is a Patient here.
export type VerifyPatientResult =
  | { status: "verified"; patientId: string }
  | { status: "ambiguous" }
  | { status: "not_verified" };

type Candidate = { patientId: string; exactFamily: boolean; exactGiven: boolean };

export async function verifyPatient(fhir: FhirClient, request: VerifyPatientRequest): Promise<VerifyPatientResult> {
  const patients = await fhir.search<Patient>("Patient", { birthdate: request.dateOfBirth, _count: "100" });
  let candidates = patients.flatMap((patient) => {
    const candidate = bestNameMatch(patient, request);
    return candidate ? [candidate] : [];
  });

  // Sounding alike is loose (Smith and Smyth match). When the Caller's words single out some candidates
  // exactly, for example after spelling the surname, those win.
  candidates = preferExact(candidates, (candidate) => candidate.exactFamily);
  candidates = preferExact(candidates, (candidate) => candidate.exactGiven);

  if (candidates.length === 0) return { status: "not_verified" };
  if (candidates.length > 1) return { status: "ambiguous" };
  return { status: "verified", patientId: candidates[0]!.patientId };
}

function bestNameMatch(patient: Patient, request: VerifyPatientRequest): Candidate | undefined {
  const matches = (patient.name ?? []).flatMap((name) => {
    const match = matchName(name, request);
    return match ? [{ patientId: patient.id!, ...match }] : [];
  });
  return matches.sort((a, b) => score(b) - score(a))[0];
}

function matchName(name: HumanName, request: VerifyPatientRequest) {
  const family = name.family ?? "";
  const given = normalizeName(name.given?.[0] ?? "");
  const statedGiven = normalizeName(request.givenName);
  if (given === "" || given[0] !== statedGiven[0] || !soundsAlike(request.familyName, family)) return undefined;
  return {
    exactFamily: normalizeName(family) === normalizeName(request.familyName),
    exactGiven: given === statedGiven,
  };
}

const score = (candidate: Candidate) => Number(candidate.exactFamily) * 2 + Number(candidate.exactGiven);

function preferExact(candidates: Candidate[], isExact: (candidate: Candidate) => boolean) {
  const exact = candidates.filter(isExact);
  return exact.length > 0 ? exact : candidates;
}
