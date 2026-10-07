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

  test.each([
    ["a date of birth that isn't a date", { givenName: "Ada", familyName: "Byron", dateOfBirth: "1815-13-10" }],
    ["a blank surname", { givenName: "Ada", familyName: " ", dateOfBirth: "1815-12-10" }],
    ["a missing given name", { familyName: "Byron", dateOfBirth: "1815-12-10" }],
  ])("%s is an invalid request that doesn't echo what was sent", async (_, body) => {
    const response = await verify(body);

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: "invalid_request" });
  });

  describe("two Patients who share a date of birth and sound alike", () => {
    let dateOfBirth: string;
    let smithId: string;
    let smythId: string;
    beforeAll(async () => {
      dateOfBirth = await unusedBirthDate();
      smithId = await createPatient({ given: ["Joanna"], family: "Smith", birthDate: dateOfBirth });
      smythId = await createPatient({ given: ["Johanna"], family: "Smyth", birthDate: dateOfBirth });
    });

    test("a name that matches both is ambiguous", async () => {
      const response = await verify({ givenName: "Jo", familyName: "Smithe", dateOfBirth });

      expect(response.status).toBe(200);
      expect(response.body).toEqual({ status: "ambiguous" });
    });

    test("the spelled-out surname picks the one it spells", async () => {
      const smith = await verify({ givenName: "Jo", familyName: "S M I T H", dateOfBirth });
      const smyth = await verify({ givenName: "Jo", familyName: "S-M-Y-T-H", dateOfBirth });

      expect(smith.body).toEqual({ status: "verified", patientId: smithId });
      expect(smyth.body).toEqual({ status: "verified", patientId: smythId });
    });

    test("the exact given name picks the one it names", async () => {
      const response = await verify({ givenName: "Johanna", familyName: "Smithe", dateOfBirth });

      expect(response.body).toEqual({ status: "verified", patientId: smythId });
    });
  });
});
