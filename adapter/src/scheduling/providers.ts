import type { FhirResource, HumanName, Practitioner, Schedule } from "fhir/r4";
import { PROVIDER_SYSTEM } from "../clinic.ts";
import type { FhirClient } from "../fhir/client.ts";

// providerId is the clinic's own key for a Provider, such as "whitfield". It never changes, unlike
// the server-assigned FHIR ids.
export type Provider = { providerId: string; providerName: string; practitionerId: string };

export type Providers = {
  all: Provider[];
  bySchedule: Map<string, Provider>;
  schedulesOf(providerId: string): string[];
};

// Every Provider with an active Schedule. A clinic has a handful, so one search finds them all.
export async function loadProviders(fhir: FhirClient): Promise<Providers> {
  const resources = await fhir.search<Schedule | Practitioner>("Schedule", {
    active: "true",
    _include: "Schedule:actor",
    _count: "200",
  });
  return providersIn(resources);
}

// The Provider of one Schedule, whether or not it still takes new Appointments.
export async function loadProviderOfSchedule(fhir: FhirClient, scheduleId: string): Promise<Providers> {
  const resources = await fhir.search<Schedule | Practitioner>("Schedule", { _id: scheduleId, _include: "Schedule:actor" });
  return providersIn(resources);
}

// The Providers of the Schedules among these resources, from the Practitioners among them.
export function providersIn(resources: FhirResource[]): Providers {
  const practitioners = new Map<string, Provider>();
  for (const resource of resources) {
    if (resource.resourceType !== "Practitioner") continue;
    const providerId = resource.identifier?.find((identifier) => identifier.system === PROVIDER_SYSTEM)?.value;
    if (!providerId) continue;
    const providerName = spokenName(resource.name?.[0]);
    practitioners.set(resource.id!, { providerId, providerName, practitionerId: resource.id! });
  }

  const bySchedule = new Map<string, Provider>();
  for (const resource of resources) {
    if (resource.resourceType !== "Schedule") continue;
    const actor = resource.actor.find((reference) => reference.reference?.startsWith("Practitioner/"));
    const provider = practitioners.get(actor?.reference?.slice("Practitioner/".length) ?? "");
    if (provider) bySchedule.set(resource.id!, provider);
  }

  return {
    all: [...new Map([...bySchedule.values()].map((provider) => [provider.providerId, provider])).values()],
    bySchedule,
    schedulesOf: (providerId) =>
      [...bySchedule].filter(([, provider]) => provider.providerId === providerId).map(([scheduleId]) => scheduleId),
  };
}

// The name the agent says aloud, such as "Dr. Marcus Whitfield".
function spokenName(name: HumanName | undefined): string {
  return [...(name?.prefix ?? []), ...(name?.given ?? []).slice(0, 1), name?.family ?? ""].join(" ").trim();
}
