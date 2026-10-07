// A pass-through to the real HAPI that answers every request late. Each request alone is quick
// enough, but a write that makes several of them one after another is not.

import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { setTimeout as sleep } from "node:timers/promises";
import { fhirBaseUrl } from "./ehr.ts";

export type DelayedEhr = { url: string; close(): Promise<void> };

export async function startDelayedEhr(delayMs: number): Promise<DelayedEhr> {
  const server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(chunk as Buffer);
    await sleep(delayMs);
    // The adapter gave up waiting, so this request never reaches the EHR.
    if (request.socket.destroyed) return;
    const answer = await fetch(request.url === "/" ? fhirBaseUrl : `${fhirBaseUrl}${request.url}`, {
      method: request.method,
      headers: { accept: "application/fhir+json", "content-type": "application/fhir+json" },
      body: chunks.length > 0 ? Buffer.concat(chunks) : undefined,
    });
    response.writeHead(answer.status, { "content-type": answer.headers.get("content-type") ?? "application/json" });
    response.end(await answer.text());
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));

  return {
    url: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.closeAllConnections();
        server.close((error) => (error ? reject(error) : resolve()));
      }),
  };
}
