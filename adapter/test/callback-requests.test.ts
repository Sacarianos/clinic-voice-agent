import type { Task } from "fhir/r4";
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { startAdapter, type Adapter } from "./support/adapter.ts";
import { createPatient, deleteAfterTests, readRecord, unusedBirthDate } from "./support/ehr.ts";

let adapter: Adapter;
beforeAll(async () => {
  adapter = await startAdapter();
});
afterAll(() => adapter.close());

async function createCallbackRequest(body: unknown) {
  const response = await adapter.post("/callback-requests", body);
  if (response.body.callbackRequestId) deleteAfterTests("Task", response.body.callbackRequestId);
  return response;
}

const inputValue = (task: Task, label: string) => {
  const input = task.input?.find((entry) => entry.type.text === label);
  return input?.valueString ?? input?.valueBoolean;
};

describe("Create Callback Request", () => {
  test("stores a Task with the phone number, the reason and the emergency flag", async () => {
    const response = await createCallbackRequest({
      phoneNumber: "+15555550123",
      reason: "Caller asked to speak to a person",
      emergency: false,
    });

    expect(response.status).toBe(201);
    const task = await readRecord<Task>("Task", response.body.callbackRequestId);
    expect(task.status).toBe("requested");
    expect(task.description).toBe("Caller asked to speak to a person");
    expect(inputValue(task, "callback phone number")).toBe("+15555550123");
    expect(inputValue(task, "emergency")).toBe(false);
    expect(task.priority).toBe("routine");
    expect(task.for).toBeUndefined();
  });

  test("an emergency Callback Request is flagged and urgent", async () => {
    const response = await createCallbackRequest({
      phoneNumber: "+15555550124",
      reason: "Caller described an emergency",
      emergency: true,
    });

    const task = await readRecord<Task>("Task", response.body.callbackRequestId);
    expect(inputValue(task, "emergency")).toBe(true);
    expect(task.priority).toBe("stat");
  });

  test("a Verified Patient's id links the Task to the Patient", async () => {
    const patientId = await createPatient({
      given: ["Rosalind"],
      family: "Okonkwo",
      birthDate: await unusedBirthDate(),
    });

    const response = await createCallbackRequest({
      phoneNumber: "+15555550125",
      reason: "Clinical question for a clinician",
      emergency: false,
      patientId,
    });

    const task = await readRecord<Task>("Task", response.body.callbackRequestId);
    expect(task.for?.reference).toBe(`Patient/${patientId}`);
  });

  test.each([
    ["no phone number", { reason: "x", emergency: false }],
    ["an empty reason", { phoneNumber: "+15555550123", reason: " ", emergency: false }],
    ["no emergency flag", { phoneNumber: "+15555550123", reason: "x" }],
  ])("%s is an invalid request", async (_name, body) => {
    const response = await createCallbackRequest(body);

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: "invalid_request" });
  });
});
