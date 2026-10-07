import { z } from "zod";
import { FAULTS, type Fault } from "./faults.ts";

const configSchema = z.object({
  PORT: z.coerce.number().int().min(0).default(3000),
  FHIR_BASE_URL: z.url().transform((url) => url.replace(/\/+$/, "")),
  // Must stay below the agent's own timeout on adapter calls, so the agent hears unknown instead of nothing.
  FHIR_TIMEOUT_MS: z.coerce.number().int().min(1).default(3000),
  // For testing only: a fault every request meets (see faults.ts).
  INJECT_FAULT: z.preprocess((value) => (value === "" ? undefined : value), z.enum(FAULTS).optional()),
});

export type Config = {
  port: number;
  fhirBaseUrl: string;
  // How long one FHIR request may take before the adapter gives up on it.
  fhirTimeoutMs: number;
  injectFault?: Fault;
};

export function loadConfig(env: Record<string, string | undefined> = process.env): Config {
  const parsed = configSchema.parse(env);
  return {
    port: parsed.PORT,
    fhirBaseUrl: parsed.FHIR_BASE_URL,
    fhirTimeoutMs: parsed.FHIR_TIMEOUT_MS,
    injectFault: parsed.INJECT_FAULT,
  };
}
