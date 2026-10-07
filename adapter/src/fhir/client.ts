// The only code in the system that speaks FHIR over HTTP. Everything above it works in domain shapes.

import type { Bundle, FhirResource } from "fhir/r4";

export type FhirClient = {
  search<T extends FhirResource>(resourceType: T["resourceType"], params: Record<string, string>): Promise<T[]>;
};

export function createFhirClient(options: { baseUrl: string }): FhirClient {
  async function get<T>(path: string): Promise<T> {
    const response = await fetch(`${options.baseUrl}/${path}`, { headers: { accept: "application/fhir+json" } });
    return (await response.json()) as T;
  }

  return {
    async search(resourceType, params) {
      const bundle = await get<Bundle>(`${resourceType}?${new URLSearchParams(params)}`);
      return (bundle.entry ?? []).map((entry) => entry.resource as never);
    },
  };
}
