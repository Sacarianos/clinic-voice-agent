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
