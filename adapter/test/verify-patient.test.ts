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
});
