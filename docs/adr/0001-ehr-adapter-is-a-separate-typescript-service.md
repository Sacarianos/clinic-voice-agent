# The EHR adapter is a separate TypeScript service

The agent never talks to the FHIR server directly. All reads and writes go through a small Hono service in `adapter/`. That service is the piece a real deployment would rewrite for each client's EHR. It also gives fault injection one obvious home, and it keeps the voice code free of FHIR details. The cost is a second language, a second toolchain and a network hop on every tool call. We accepted that because an in-process Python module would blur the boundary we most want to show.

## Considered Options

- Python module inside the agent: less code and no hop, but nothing forces the agent to stay ignorant of FHIR.
- Fastify: more mature, more ceremony. Hono with zod is smaller for a service this size.
