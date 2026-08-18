/**
 * Read facade over both case stores.
 *
 * `lib/store.ts` holds fixtures and in-memory intake cases; `lib/live/
 * live-store.ts` holds cases running on the deployed Agent Runtime. This
 * module is the one place that knows about both, which keeps `store.ts`
 * free of any live-engine import and avoids a cycle (`live-store.ts`
 * already imports from `store.ts`).
 */

import { listLiveCases } from "./live/live-store";
import { liveEngineResourceName } from "./live/engine-client";
import { listCases as listLocalCases } from "./store";
import type { CaseQueueItem } from "./types";

export { getAnyCase } from "./live/live-store";

/** Fixture + intake + live cases, one queue, sorted by batch. */
export function listAllCases(): CaseQueueItem[] {
  return [...listLocalCases(), ...listLiveCases()].sort((a, b) =>
    a.batch_id.localeCompare(b.batch_id),
  );
}

export function liveEngineConfigured(): boolean {
  return liveEngineResourceName() !== null;
}
