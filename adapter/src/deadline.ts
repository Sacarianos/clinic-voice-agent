// One deadline per adapter request, shared by every FHIR call made to answer it. A write that makes
// five FHIR calls gets the same time as a read that makes one, so the agent can count on an answer
// within the deadline: settled, or unknown when the EHR is too slow to tell.

import { AsyncLocalStorage } from "node:async_hooks";
import type { MiddlewareHandler } from "hono";

const deadline = new AsyncLocalStorage<AbortSignal>();

// Runs the rest of the request under a deadline that aborts its FHIR calls once deadlineMs have passed.
export function answerWithin(deadlineMs: number): MiddlewareHandler {
  return (c, next) => deadline.run(AbortSignal.timeout(deadlineMs), next);
}

// The deadline of the request being answered.
export function requestDeadline(): AbortSignal {
  const signal = deadline.getStore();
  if (!signal) throw new Error("FHIR calls are made only while answering a request");
  return signal;
}
