// A Slot that a write takes records the write's idempotency key. If only part of the write lands,
// the same write sent again knows the busy Slot is its own and finishes, where any other write
// finds it taken. A Slot released from a write that failed records that write's key instead.

import type { Slot } from "fhir/r4";

const HELD_FOR = "https://clinic.example/fhir/StructureDefinition/slot-held-for-write";
const RELEASED_FROM = "https://clinic.example/fhir/StructureDefinition/slot-released-from-write";

export const isHeldFor = (slot: Slot, idempotencyKey: string) =>
  slot.extension?.some((extension) => extension.url === HELD_FOR && extension.valueString === idempotencyKey) ?? false;

export function taken(slot: Slot, idempotencyKey: string): Slot {
  return marked(freed(slot), "busy", HELD_FOR, idempotencyKey);
}

export function freed(slot: Slot): Slot {
  const { extension, ...rest } = slot;
  const others = extension?.filter((item) => item.url !== HELD_FOR && item.url !== RELEASED_FROM) ?? [];
  return { ...rest, status: "free", ...(others.length > 0 ? { extension: others } : {}) };
}

// Free, and changed even when it was free already, so a write that read the Slot before can no
// longer commit against its version.
export function released(slot: Slot, idempotencyKey: string): Slot {
  return marked(freed(slot), "free", RELEASED_FROM, idempotencyKey);
}

function marked(slot: Slot, status: Slot["status"], url: string, idempotencyKey: string): Slot {
  const { extension = [], ...rest } = slot;
  return { ...rest, status, extension: [...extension, { url, valueString: idempotencyKey }] };
}
