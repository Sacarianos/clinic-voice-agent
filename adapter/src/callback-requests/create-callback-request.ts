import type { Task } from "fhir/r4";
import type { FhirClient } from "../fhir/client.ts";

export type CreateCallbackRequest = {
  phoneNumber: string;
  reason: string;
  emergency: boolean;
  patientId?: string | undefined;
};

// A Callback Request is a Task for clinic staff. A Task has no phone field, so the number and the
// emergency flag ride as labelled inputs.
export async function createCallbackRequest(fhir: FhirClient, request: CreateCallbackRequest) {
  const task = await fhir.create<Task>({
    resourceType: "Task",
    status: "requested",
    intent: "order",
    priority: request.emergency ? "stat" : "routine",
    description: request.reason,
    authoredOn: new Date().toISOString(),
    ...(request.patientId && { for: { reference: `Patient/${request.patientId}` } }),
    input: [
      { type: { text: "callback phone number" }, valueString: request.phoneNumber },
      { type: { text: "emergency" }, valueBoolean: request.emergency },
    ],
  });
  return { callbackRequestId: task.id };
}
