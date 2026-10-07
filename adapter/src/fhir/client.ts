// The only code in the system that speaks FHIR over HTTP. Everything above it works in domain shapes.

import type { Bundle, FhirResource } from "fhir/r4";

// The EHR didn't give a usable answer: unreachable, or an error status. Routes turn it into ehr_unavailable.
// Messages name the resource type only. Search parameters carry names and dates of birth.
export class EhrUnavailableError extends Error {
  override name = "EhrUnavailableError";
}

export type FhirClient = {
  search<T extends FhirResource>(resourceType: T["resourceType"], params: Record<string, string>): Promise<T[]>;
  create<T extends FhirResource>(resource: T): Promise<T & { id: string }>;
};

export function createFhirClient(options: { baseUrl: string }): FhirClient {
  async function get<T>(resourceType: string, query: string): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${options.baseUrl}/${resourceType}?${query}`, {
        headers: { accept: "application/fhir+json" },
      });
    } catch (error) {
      throw new EhrUnavailableError(`GET ${resourceType} failed: ${(error as Error).cause ?? error}`);
    }
    if (!response.ok) throw new EhrUnavailableError(`GET ${resourceType} returned ${response.status}`);
    return (await response.json()) as T;
  }

  async function create<T extends FhirResource>(resource: T): Promise<T & { id: string }> {
    let response: Response;
    try {
      response = await fetch(`${options.baseUrl}/${resource.resourceType}`, {
        method: "POST",
        headers: { accept: "application/fhir+json", "content-type": "application/fhir+json" },
        body: JSON.stringify(resource),
      });
    } catch (error) {
      throw new EhrUnavailableError(`POST ${resource.resourceType} failed: ${(error as Error).cause ?? error}`);
    }
    if (!response.ok) throw new EhrUnavailableError(`POST ${resource.resourceType} returned ${response.status}`);
    return (await response.json()) as T & { id: string };
  }

  return {
    async search(resourceType, params) {
      const bundle = await get<Bundle>(resourceType, new URLSearchParams(params).toString());
      return (bundle.entry ?? []).map((entry) => entry.resource as never);
    },
    create,
  };
}
