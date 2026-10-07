import type { Appointment, Slot } from "fhir/r4";
import type { FhirClient } from "../fhir/client.ts";
import { versionGuardedWrite, type WriteOutcome } from "../write-outcome.ts";
import { isHeldFor, released } from "./slot-holds.ts";

export type ReleaseSlotRejection = "write_landed" | "slot_not_found";

export type ReleaseSlotResult = WriteOutcome<object, ReleaseSlotRejection>;

// Settles a Book or Reschedule that failed after an unknown answer: it may have landed in part,
// leaving its Slot busy, or still be on its way to the EHR. Releasing frees the Slot if the write
// holds it, and changes it either way, so a write still on its way, guarded by the Slot version it
// read, can never commit. Succeeded means the write holds the Slot no longer and never will. A
// write that did land in full is left alone and answers rejected with write_landed.
export function releaseSlot(fhir: FhirClient, slotId: string, idempotencyKey: string): Promise<ReleaseSlotResult> {
  return versionGuardedWrite<ReleaseSlotResult>(async () => {
    const slot = await fhir.read<Slot>("Slot", slotId);
    if (!slot) return { outcome: "rejected", reason: "slot_not_found" };
    const heldForThisWrite = isHeldFor(slot, idempotencyKey);
    // Someone else's since this write read it, so this write can't commit against it.
    if (slot.status === "busy" && !heldForThisWrite) return { outcome: "succeeded" };
    if (heldForThisWrite && (await bookedIn(fhir, slotId))) return { outcome: "rejected", reason: "write_landed" };

    const result = await fhir.transaction({
      resourceType: "Bundle",
      type: "transaction",
      entry: [
        {
          resource: released(slot, idempotencyKey),
          request: { method: "PUT", url: `Slot/${slot.id}`, ifMatch: `W/"${slot.meta?.versionId}"` },
        },
      ],
    });
    if (result.status === "committed") return { outcome: "succeeded" };
    if (result.status === "conflict") return "conflict";
    return { outcome: result.status };
  });
}

async function bookedIn(fhir: FhirClient, slotId: string): Promise<boolean> {
  const appointments = await fhir.search<Appointment>("Appointment", { slot: `Slot/${slotId}`, status: "booked" });
  return appointments.length > 0;
}
