import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import { createPatient, unusedBirthDate } from "./support/ehr.ts";

let adapter: Adapter;
beforeAll(async () => {
  adapter = await startAdapter();
});
afterAll(() => adapter.close());

const verify = (body: unknown) => adapter.post("/patients/verify", body);

describe("Verify patient", () => {
  test("exact name and date of birth verify the Patient", async () => {
    const dateOfBirth = await unusedBirthDate();
    const patientId = await createPatient({ given: ["Rosalind"], family: "Okonkwo", birthDate: dateOfBirth });

    const response = await verify({ givenName: "Rosalind", familyName: "Okonkwo", dateOfBirth });

    expect(response.status).toBe(200);
    expect(response.body).toEqual({ status: "verified", patientId });
  });

  test.each([
    ["Smith", "Smyth"],
    ["Wuckert", "Vuckert"],
    ["O'Keefe", "okeefe"],
    ["Jaskolski", "Yaskolski"],
  ])("a surname that sounds like %s verifies the Patient when heard as %s", async (family, heard) => {
    const dateOfBirth = await unusedBirthDate();
    const patientId = await createPatient({ given: ["Margarethe"], family, birthDate: dateOfBirth });

    const response = await verify({ givenName: "Margarethe", familyName: heard, dateOfBirth });

    expect(response.body).toEqual({ status: "verified", patientId });
  });

  test("a given name matches by its first letter", async () => {
    const dateOfBirth = await unusedBirthDate();
    const patientId = await createPatient({ given: ["Katherine", "Ann"], family: "Lindqvist", birthDate: dateOfBirth });

    const response = await verify({ givenName: "kate", familyName: "Lindqvist", dateOfBirth });

    expect(response.body).toEqual({ status: "verified", patientId });
  });

  test("a wrong date of birth, an unknown name and a wrong given name all get the same not-verified response", async () => {
    const dateOfBirth = await unusedBirthDate();
    const otherDateOfBirth = await unusedBirthDate();
    await createPatient({ given: ["Theodora"], family: "Abernathy", birthDate: dateOfBirth });

    const responses = await Promise.all([
      verify({ givenName: "Theodora", familyName: "Abernathy", dateOfBirth: otherDateOfBirth }),
      verify({ givenName: "Bartholomew", familyName: "Quigley", dateOfBirth }),
      verify({ givenName: "Dorothy", familyName: "Abernathy", dateOfBirth }),
    ]);

    for (const response of responses) {
      expect(response.status).toBe(200);
      expect(response.body).toEqual({ status: "not_verified" });
    }
  });
});
