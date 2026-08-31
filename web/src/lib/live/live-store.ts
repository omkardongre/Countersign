/**
 * Live-case bookkeeping: the desk's cached record plus the engine session
 * pointer needed to poll or resume it. Deliberately separate from `lib/store.ts`
 * so the fixture/intake path stays unaware the live path exists, matching the
 * existing `origin: "fixture" | "intake"` split.
 *
 * The source of truth for graph events is the deployed Agent Runtime's
 * Vertex-managed session store. Firestore persists the case-to-session pointer
 * and projected desk record so Vercel serverless instances can recover them;
 * the process-local map is only a cache.
 */

import { getCase as getFixtureOrIntakeCase, toQueueItem } from "@/lib/store";
import type {
  CaseDeskRecord,
  CaseIntake,
  CaseQueueItem,
  ClassifiedCase,
} from "@/lib/types";
import {
  AgentRuntimeClient,
  getGoogleCloudAccessToken,
  liveEngineResourceName,
  type EngineEvent,
} from "./engine-client";
import { type PendingInterrupt, projectLiveEvents } from "./projector";

type LiveCaseSession = {
  engineResource: string;
  userId: string;
  sessionId: string;
  pendingInterrupt: PendingInterrupt | null;
  /** Events from the separate CFI Runtime, when its poller is configured. */
  cfiEvents: EngineEvent[];
  record: CaseDeskRecord;
};

type LiveGlobalStore = {
  sessions: Map<string, LiveCaseSession>;
};

function globalLiveStore(): LiveGlobalStore {
  const g = globalThis as typeof globalThis & {
    __countersignLive?: LiveGlobalStore;
  };
  if (!g.__countersignLive) {
    g.__countersignLive = { sessions: new Map() };
  }
  return g.__countersignLive;
}

const FIRESTORE_PROJECT = "countersign-505107";
const FIRESTORE_COLLECTION = "countersign_web_live_cases";

function firestoreDocumentUrl(caseId: string): string {
  return `https://firestore.googleapis.com/v1/projects/${FIRESTORE_PROJECT}/databases/(default)/documents/${FIRESTORE_COLLECTION}/${encodeURIComponent(caseId)}`;
}

async function saveLiveSession(caseId: string, live: LiveCaseSession): Promise<void> {
  const token = await getGoogleCloudAccessToken();
  const response = await fetch(firestoreDocumentUrl(caseId), {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ fields: { payload: { stringValue: JSON.stringify(live) } } }),
  });
  if (!response.ok) {
    throw new Error(`Failed to persist live case pointer: ${response.status} ${await response.text()}`);
  }
}

async function loadLiveSession(caseId: string): Promise<LiveCaseSession | null> {
  const cached = globalLiveStore().sessions.get(caseId);
  if (cached) return cached;
  const token = await getGoogleCloudAccessToken();
  const response = await fetch(firestoreDocumentUrl(caseId), {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`Failed to load live case pointer: ${response.status} ${await response.text()}`);
  }
  const document = (await response.json()) as {
    fields?: { payload?: { stringValue?: string } };
  };
  const payload = document.fields?.payload?.stringValue;
  if (!payload) throw new Error(`Live case pointer ${caseId} has no payload.`);
  const live = JSON.parse(payload) as LiveCaseSession;
  globalLiveStore().sessions.set(caseId, live);
  return live;
}

export function liveEngineConfigured(): boolean {
  return liveEngineResourceName() !== null;
}

/** Reads a case that may live in either the fixture/intake store or here. */
export async function getAnyCase(caseId: string): Promise<CaseDeskRecord | null> {
  const local = getFixtureOrIntakeCase(caseId);
  if (local) return local;
  return (await loadLiveSession(caseId))?.record ?? null;
}

export function getLiveSessionPointer(caseId: string) {
  const live = globalLiveStore().sessions.get(caseId);
  if (!live) return null;
  return {
    engine_resource: live.engineResource,
    user_id: live.userId,
    session_id: live.sessionId,
    pending_interrupt: live.pendingInterrupt
      ? {
          interrupt_id: live.pendingInterrupt.interruptId,
          function_name: live.pendingInterrupt.functionName,
          node: live.pendingInterrupt.node,
          message: live.pendingInterrupt.message,
        }
      : null,
  };
}

/**
 * Opens a case on the deployed Agent Runtime: creates a session, submits the
 * `DeviationCaseRequest`, drains the resulting stream, and projects it onto
 * the desk's `CaseDeskRecord` shape.
 *
 * Rejects rather than retries on any engine failure (a denied Gateway call,
 * a network error, a malformed graph payload) — a live case that failed to
 * open must show as failed, not silently fall back to fixture data.
 */
export async function openLiveCase(args: {
  intake: CaseIntake;
  classified: ClassifiedCase;
  evidence: {
    sourceUri: string;
    mimeType: string;
    contentBase64: string;
    /**
     * Google Drive file id. When set, `collect_manufacturing` pulls the
     * real page over the `drive_fetch_evidence` MCP tool and ignores
     * `mimeType`/`contentBase64` here entirely (see
     * `app/domain/branches.py`) — same pull pattern as the QC/supply
     * collectors, instead of trusting whatever this route pushes in.
     */
    driveFileId?: string;
  };
}): Promise<CaseDeskRecord> {
  const engineResource = liveEngineResourceName();
  if (!engineResource) {
    throw new Error(
      "COUNTERSIGN_CASE_ENGINE_URL is not set; live cases cannot be opened.",
    );
  }
  const client = new AgentRuntimeClient(engineResource);
  const userId = `desk:${args.classified.tenant_id}`;
  const session = await client.createSession(userId);

  const events = await client.start(userId, session.id, {
    intake: args.intake,
    evidence: {
      source_uri: args.evidence.sourceUri,
      mime_type: args.evidence.mimeType,
      content_base64: args.evidence.contentBase64,
      page_count: 1,
      drive_file_id: args.evidence.driveFileId,
    },
  });

  const { record, pendingInterrupt } = projectLiveEvents(events, {
    intake: args.intake,
    classified: args.classified,
  });
  record.origin = "live";

  const live: LiveCaseSession = {
    engineResource,
    userId,
    sessionId: session.id,
    pendingInterrupt,
    cfiEvents: [],
    record,
  };
  globalLiveStore().sessions.set(args.classified.case_id, live);
  await saveLiveSession(args.classified.case_id, live);
  return record;
}

/**
 * Re-reads the live session's events and re-projects the desk record,
 * without submitting anything. This is the poll side of "poll, don't hold
 * SSE" (`11` §1): the desk calls this on an interval while a case is
 * `sterility_hold` or otherwise between confirmed pauses.
 */
export async function pollLiveCase(caseId: string): Promise<CaseDeskRecord | null> {
  const live = await loadLiveSession(caseId);
  if (!live) return null;
  const client = new AgentRuntimeClient(live.engineResource);
  const session = await client.getSession(live.userId, live.sessionId);
  if (!session) return live.record;

  const { record, pendingInterrupt } = projectLiveEvents(
    session.events,
    {
      intake: live.record.intake,
      classified: live.record.classified,
    },
    { cfiEvents: live.cfiEvents },
  );
  record.origin = "live";
  live.pendingInterrupt = pendingInterrupt;
  live.record = record;
  await saveLiveSession(caseId, live);
  return record;
}

/**
 * Resumes a paused live case by answering its current `RequestInput`
 * interrupt (QP confirmation or a sterility tick). Fails if the case has no
 * pending interrupt — resuming an invocation that is not actually paused is
 * a client bug, not a retryable condition.
 */
export async function resumeLiveCase(
  caseId: string,
  response: Record<string, unknown>,
): Promise<CaseDeskRecord> {
  const live = await loadLiveSession(caseId);
  if (!live) {
    throw new Error(`No live session for case ${caseId}.`);
  }
  if (!live.pendingInterrupt) {
    throw new Error(`Case ${caseId} has no pending interrupt to resume.`);
  }
  const client = new AgentRuntimeClient(live.engineResource);
  const events = await client.resume(
    live.userId,
    live.sessionId,
    live.pendingInterrupt.interruptId,
    live.pendingInterrupt.functionName,
    response,
  );
  const { record, pendingInterrupt } = projectLiveEvents(
    events,
    {
      intake: live.record.intake,
      classified: live.record.classified,
    },
    { cfiEvents: live.cfiEvents },
  );
  record.origin = "live";
  live.pendingInterrupt = pendingInterrupt;
  live.record = record;
  await saveLiveSession(caseId, live);
  return record;
}

/**
 * Reprojects a live case after a separate CFI Runtime poll has returned its
 * event history. The CFI handoff and Runtime remain separate trust
 * boundaries; this function only joins already-authenticated event streams
 * for presentation and never starts or resumes the CFI itself.
 */
export async function updateLiveCfiEvents(
  caseId: string,
  cfiEvents: EngineEvent[],
): Promise<CaseDeskRecord | null> {
  const live = await loadLiveSession(caseId);
  if (!live) return null;
  const client = new AgentRuntimeClient(live.engineResource);
  const session = await client.getSession(live.userId, live.sessionId);
  if (!session) return live.record;
  live.cfiEvents = cfiEvents;
  const { record, pendingInterrupt } = projectLiveEvents(
    session.events,
    {
      intake: live.record.intake,
      classified: live.record.classified,
    },
    { cfiEvents: live.cfiEvents },
  );
  record.origin = "live";
  live.pendingInterrupt = pendingInterrupt;
  live.record = record;
  await saveLiveSession(caseId, live);
  return record;
}

export function isLiveCase(caseId: string): boolean {
  return globalLiveStore().sessions.has(caseId);
}

/** Queue rows for live cases, so they appear alongside fixtures. */
export function listLiveCases(): CaseQueueItem[] {
  return [...globalLiveStore().sessions.values()].map((s) => toQueueItem(s.record));
}
