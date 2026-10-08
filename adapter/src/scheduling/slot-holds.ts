// A Slot that a write takes records the write's idempotency key. If only part of the write lands,
// the same write sent again knows the busy Slot is its own and finishes, where any other write
// finds it taken.

import type { Slot } from "fhir/r4";

const HELD_FOR = "https://clinic.example/fhir/StructureDefinition/slot-held-for-write";

export const isHeldFor = (slot: Slot, idempotencyKey: string) =>
  slot.extension?.some((extension) => extension.url === HELD_FOR && extension.valueString === idempotencyKey) ?? false;

export function taken(slot: Slot, idempotencyKey: string): Slot {
  const { extension = [], ...rest } = freed(slot);
  return { ...rest, status: "busy", extension: [...extension, { url: HELD_FOR, valueString: idempotencyKey }] };
}

export function freed(slot: Slot): Slot {
  const { extension, ...rest } = slot;
  const others = extension?.filter((item) => item.url !== HELD_FOR) ?? [];
  return { ...rest, status: "free", ...(others.length > 0 ? { extension: others } : {}) };
}
