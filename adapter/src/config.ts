import { z } from "zod";

const configSchema = z.object({
  PORT: z.coerce.number().int().min(0).default(3000),
  FHIR_BASE_URL: z.url().transform((url) => url.replace(/\/+$/, "")),
});

export type Config = {
  port: number;
  fhirBaseUrl: string;
};

export function loadConfig(env: Record<string, string | undefined> = process.env): Config {
  const parsed = configSchema.parse(env);
  return { port: parsed.PORT, fhirBaseUrl: parsed.FHIR_BASE_URL };
}
