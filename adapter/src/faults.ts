// Fault injection: makes the EHR misbehave on purpose, so every failure the agent must handle can be
// tested. A fault applies to one adapter request when it carries the x-inject-fault header, or to
// every request when INJECT_FAULT is set. It acts between the adapter and the EHR, so the adapter's
// own code meets it the way it would meet a real fault:
// - timeout: the EHR applies each write but answers too late. Writes come back unknown.
// - server_error: the EHR answers every request with a 500 and applies nothing. Writes come back
//   failed, and reads ehr_unavailable.
// - slot_taken: someone else takes the Slot a write is about to take, just before it commits. Book
//   and Reschedule come back rejected with slot_taken. Cancel takes no Slot and is unaffected.
// - half_write: the EHR applies only the first half of a write's transaction, then the connection
//   drops. Writes come back unknown, and the same write sent again finishes the job.

import { AsyncLocalStorage } from "node:async_hooks";
import type { Bundle, FhirResource, OperationOutcome, Slot } from "fhir/r4";
import type { MiddlewareHandler } from "hono";
import { freed } from "./scheduling/slot-holds.ts";

export const FAULTS = ["timeout", "server_error", "slot_taken", "half_write"] as const;
export type Fault = (typeof FAULTS)[number];

export const FAULT_HEADER = "x-inject-fault";

export type Fetch = (url: string, init: RequestInit) => Promise<Response>;

const currentFault = new AsyncLocalStorage<Fault>();

const isFault = (value: string): value is Fault => (FAULTS as readonly string[]).includes(value);

// Runs the rest of the request with its fault, if it has one.
export function injectFaults(everyRequest: Fault | undefined): MiddlewareHandler {
  return async (c, next) => {
    const asked = c.req.header(FAULT_HEADER);
    if (asked !== undefined && !isFault(asked)) return c.json({ error: "invalid_request" }, 400);
    const fault = asked ?? everyRequest;
    if (fault) await currentFault.run(fault, next);
    else await next();
  };
}

// fetch, as the current request's fault would have the EHR answer it.
export const fetchWithFaults: Fetch = async (url, init) => {
  const fault = currentFault.getStore();
  const isWrite = init.method !== "GET";
  if (fault === "server_error") return serverError();
  if (fault === "timeout" && isWrite) return answerTooLate(url, init);
  const transaction = isWrite ? transactionIn(init) : undefined;
  if (fault === "half_write" && transaction) return applyFirstHalf(url, init, transaction);
  if (fault === "slot_taken" && transaction) await takeSlotsFirst(url, init, transaction);
  return fetch(url, init);
};

function serverError(): Response {
  const outcome: OperationOutcome = {
    resourceType: "OperationOutcome",
    issue: [{ severity: "error", code: "exception", diagnostics: "Injected server error" }],
  };
  return new Response(JSON.stringify(outcome), { status: 500, headers: { "content-type": "application/fhir+json" } });
}

async function answerTooLate(url: string, init: RequestInit): Promise<Response> {
  const response = await fetch(url, init);
  await response.body?.cancel();
  const signal = init.signal!;
  return new Promise((_, reject) => {
    if (signal.aborted) reject(signal.reason);
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
}

function transactionIn(init: RequestInit): Bundle | undefined {
  const body = JSON.parse(String(init.body)) as FhirResource;
  return body.resourceType === "Bundle" && body.type === "transaction" ? body : undefined;
}

async function applyFirstHalf(url: string, init: RequestInit, transaction: Bundle): Promise<Response> {
  const entries = transaction.entry ?? [];
  const firstHalf = { ...transaction, entry: entries.slice(0, Math.max(1, Math.floor(entries.length / 2))) };
  const response = await fetch(url, { ...init, body: JSON.stringify(firstHalf) });
  if (!response.ok) return response;
  await response.body?.cancel();
  throw new TypeError("fetch failed", { cause: { code: "ECONNRESET" } });
}

// Another booking marks the Slot busy, so the write's version guard on it no longer holds.
async function takeSlotsFirst(baseUrl: string, init: RequestInit, transaction: Bundle) {
  for (const entry of transaction.entry ?? []) {
    const resource = entry.resource as Slot | undefined;
    if (entry.request?.method !== "PUT" || resource?.resourceType !== "Slot" || resource.status !== "busy") continue;
    const url = `${baseUrl}/${entry.request.url}`;
    const headers = { accept: "application/fhir+json", "content-type": "application/fhir+json" };
    const current = (await (await fetch(url, { headers, signal: init.signal })).json()) as Slot;
    const takenBySomeoneElse = { ...freed(current), status: "busy" };
    await fetch(url, { method: "PUT", headers, body: JSON.stringify(takenBySomeoneElse), signal: init.signal });
  }
}
