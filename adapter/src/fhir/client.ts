// The only code in the system that speaks FHIR over HTTP. Everything above it works in domain shapes.

import type { Bundle, FhirResource } from "fhir/r4";

// The EHR didn't give a usable answer: unreachable, or an error status. Routes turn it into ehr_unavailable.
// Messages name the resource type only. Search parameters carry names and dates of birth.
export class EhrUnavailableError extends Error {
  override name = "EhrUnavailableError";
}

// Repeat a name to send it more than once, as in [["start", "ge..."], ["start", "lt..."]].
export type SearchParams = Record<string, string> | [string, string][];

export type FhirClient = {
  // The first page of matches.
  search<T extends FhirResource>(resourceType: T["resourceType"], params: SearchParams): Promise<T[]>;
  // Every match, fetching further pages only as the caller asks for more.
  searchEach<T extends FhirResource>(resourceType: T["resourceType"], params: SearchParams): AsyncGenerator<T>;
  // Undefined when there is no such resource, or it was deleted.
  read<T extends FhirResource>(resourceType: T["resourceType"], id: string): Promise<T | undefined>;
};

export function createFhirClient(options: { baseUrl: string }): FhirClient {
  async function request(method: string, url: string, what: string): Promise<Response> {
    try {
      return await fetch(url, { method, headers: { accept: "application/fhir+json" } });
    } catch (error) {
      throw new EhrUnavailableError(`${method} ${what} failed: ${(error as Error).cause ?? error}`);
    }
  }

  async function getBundle(url: string, resourceType: string): Promise<Bundle> {
    const response = await request("GET", url, resourceType);
    if (!response.ok) throw new EhrUnavailableError(`GET ${resourceType} returned ${response.status}`);
    return (await response.json()) as Bundle;
  }

  const searchUrl = (resourceType: string, params: SearchParams) =>
    `${options.baseUrl}/${resourceType}?${new URLSearchParams(params)}`;

  return {
    async search(resourceType, params) {
      const bundle = await getBundle(searchUrl(resourceType, params), resourceType);
      return (bundle.entry ?? []).map((entry) => entry.resource as never);
    },

    async *searchEach(resourceType, params) {
      let url: string | undefined = searchUrl(resourceType, params);
      while (url) {
        const bundle = await getBundle(url, resourceType);
        for (const entry of bundle.entry ?? []) yield entry.resource as never;
        url = bundle.link?.find((link) => link.relation === "next")?.url;
      }
    },

    async read(resourceType, id) {
      const response = await request("GET", `${options.baseUrl}/${resourceType}/${encodeURIComponent(id)}`, resourceType);
      if (response.status === 404 || response.status === 410) return undefined;
      if (!response.ok) throw new EhrUnavailableError(`GET ${resourceType} returned ${response.status}`);
      return (await response.json()) as never;
    },
  };
}
