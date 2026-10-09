import { z } from "zod";
import { FAULTS, type Fault } from "./faults.ts";

const configSchema = z.object({
  PORT: z.coerce.number().int().min(0).default(3000),
  FHIR_BASE_URL: z.url().transform((url) => url.replace(/\/+$/, "")),
  // The agent counts on it, as ADAPTER_DEADLINE_SECS in its timeouts module, and waits a little longer
  // than this, so it always hears an outcome before it stops waiting. /healthz reports it, and the
  // agent's tests fail when the two differ.
  REQUEST_DEADLINE_MS: z.coerce.number().int().min(1).default(4000),
  // For testing only: a fault every request meets. faults.ts lists them.
  INJECT_FAULT: z.preprocess((value) => (value === "" ? undefined : value), z.enum(FAULTS).optional()),
});

export type Config = {
  port: number;
  fhirBaseUrl: string;
  // How long the adapter may take to answer any request, every FHIR call it makes included.
  requestDeadlineMs: number;
  injectFault?: Fault;
};

export function loadConfig(env: Record<string, string | undefined> = process.env): Config {
  const parsed = configSchema.parse(env);
  return {
    port: parsed.PORT,
    fhirBaseUrl: parsed.FHIR_BASE_URL,
    requestDeadlineMs: parsed.REQUEST_DEADLINE_MS,
    injectFault: parsed.INJECT_FAULT,
  };
}
