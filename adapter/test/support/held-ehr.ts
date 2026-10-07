// A pass-through to the real HAPI that holds back the next FHIR transaction until the test releases
// it. An adapter pointed here has done all its reads when the hold starts, so a test can land another
// write in exactly the gap between a write's reads and its commit, every time.

import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { fhirBaseUrl } from "./ehr.ts";

export type HeldEhr = {
  url: string;
  // Resolves once the next transaction has arrived and is being held.
  held: Promise<void>;
  release(): void;
  close(): Promise<void>;
};

export async function startHeldEhr(): Promise<HeldEhr> {
  let holding: () => void;
  const held = new Promise<void>((resolve) => (holding = resolve));
  let release: () => void;
  const released = new Promise<void>((resolve) => (release = resolve));
  let holdNext = true;

  const server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(chunk as Buffer);
    const isTransaction = request.method === "POST" && request.url === "/";
    if (isTransaction && holdNext) {
      holdNext = false;
      holding();
      await released;
      // The adapter gave up waiting, so the held write never reaches the EHR.
      if (request.socket.destroyed) return;
    }
    const answer = await fetch(isTransaction ? fhirBaseUrl : `${fhirBaseUrl}${request.url}`, {
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
    held,
    release: () => release(),
    close: () => new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
  };
}
