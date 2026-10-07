import { fhirBaseUrl } from "./ehr.ts";

const READY_WITHIN_MS = 180_000;

// Adapter tests run against a real HAPI. Wait for it so a cold container doesn't fail the first test.
export default async function waitForEhr() {
  const deadline = Date.now() + READY_WITHIN_MS;
  let lastError: unknown;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${fhirBaseUrl}/metadata?_summary=true`);
      if (response.ok) return;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
  throw new Error(
    `HAPI is not reachable at ${fhirBaseUrl} (${String(lastError)}). ` +
      "Run: docker compose -f infra/compose.yaml up -d --wait --build, and set FHIR_BASE_URL if HAPI is not on port 8080.",
  );
}
