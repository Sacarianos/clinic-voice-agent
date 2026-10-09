import type { Appointment, BundleEntry, Slot } from "fhir/r4";
import { EhrUnavailableError, type FhirClient } from "./fhir/client.ts";

// Every write answers with one of four outcomes, always with HTTP 200, and the agent acts on each
// differently:
// - succeeded: written and confirmed. Only now may the agent tell the Caller it is done.
// - rejected: a business rule stopped it, such as a Slot already taken. Nothing was written. No retry.
// - failed: nothing was written. Safe to retry once with the same idempotency key.
// - unknown: it may or may not have been written. Read the EHR again before saying anything.
export type WriteOutcome<Success extends object, Rejection extends string> =
  | ({ outcome: "succeeded" } & Success)
  | { outcome: "rejected"; reason: Rejection }
  | { outcome: "failed" }
  | { outcome: "unknown" };

// Runs a write that reads the EHR and then commits a transaction guarded by the versions it read.
// A conflict means something changed in between, perhaps a racing retry of this same write, so it
// reads and decides once more. If the reads fail or the conflict repeats, nothing was written.
export async function versionGuardedWrite<Outcome extends WriteOutcome<object, string>>(
  attempt: () => Promise<Outcome | "conflict">,
): Promise<Outcome | { outcome: "failed" }> {
  try {
    for (let tries = 0; tries < 2; tries++) {
      const result = await attempt();
      if (result !== "conflict") return result;
    }
    return { outcome: "failed" };
  } catch (error) {
    if (error instanceof EhrUnavailableError) return { outcome: "failed" };
    throw error;
  }
}

// A transaction entry that writes the resource back, guarded by the version it was read at, so a
// write that changed it since then makes the whole transaction conflict.
export function guardedPut<Resource extends Slot | Appointment>(resource: Resource): BundleEntry<Resource> {
  return {
    resource,
    request: { method: "PUT", url: `${resource.resourceType}/${resource.id}`, ifMatch: `W/"${resource.meta?.versionId}"` },
  };
}

// Commits the entries in one transaction. Committed answers succeeded(), a conflict sends
// versionGuardedWrite round to read and decide again, and anything else is what came of it.
export async function commitGuarded<Success extends { outcome: "succeeded" }>(
  fhir: FhirClient,
  entries: BundleEntry<Slot | Appointment>[],
  succeeded: () => Success,
): Promise<Success | "conflict" | { outcome: "failed" | "unknown" }> {
  const result = await fhir.transaction({ resourceType: "Bundle", type: "transaction", entry: entries });
  if (result.status === "committed") return succeeded();
  if (result.status === "conflict") return "conflict";
  return { outcome: result.status };
}
