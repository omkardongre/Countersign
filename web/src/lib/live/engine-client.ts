/**
 * Minimal REST client for the deployed Countersign case Agent Runtime.
 *
 * Talks directly to the Vertex AI `reasoningEngines` REST surface (the same
 * surface `infra/geap/verify_governance.sh` drives with `curl` + `gcloud auth
 * print-access-token`) rather than pulling in the Python-only
 * `google-cloud-aiplatform` SDK. Auth is Application Default Credentials via
 * `google-auth-library`, so this runs under the same `gcloud auth
 * application-default login` the rest of this repo's tooling already uses.
 *
 * Two request shapes only, matching the reasoningEngine's registered
 * operations (see `infra/geap/evidence/reasoning_engine.json`):
 *   - non-streaming `:query`  -> {"class_method": "...", "input": {...}}
 *   - streaming     `:streamQuery?alt=sse` -> same body, response is a raw
 *     stream of back-to-back JSON objects (see `json-stream.ts` — despite
 *     `alt=sse` this is NOT `data: ` framed SSE; verified against
 *     `infra/geap/evidence/stream_allowed.sse`).
 *
 * Per `final_reseach/ARCHITECTURE.md` §4.4, a single streamQuery call has a
 * 15-minute ceiling. The deviation-case graph reaches a `RequestInput`
 * interrupt (sterility hold or QP pause) well inside that window on a first
 * run, so this client drains exactly one stream per case action and never
 * holds a connection open waiting for the 14-day sterility hold — that wait
 * is observed by polling `getSession`, not by streaming (`10` P0, `11` §1).
 */

import { GoogleAuth } from "google-auth-library";

const API_VERSION = "v1beta1";

export type EngineEvent = Record<string, unknown>;

export type EngineFunctionCall = {
  name: string;
  id: string;
  args: Record<string, unknown>;
};

let cachedAuth: GoogleAuth | null = null;

function getAuth(): GoogleAuth {
  if (!cachedAuth) {
    cachedAuth = new GoogleAuth({
      scopes: ["https://www.googleapis.com/auth/cloud-platform"],
    });
  }
  return cachedAuth;
}

async function getAccessToken(): Promise<string> {
  const client = await getAuth().getClient();
  const token = await client.getAccessToken();
  if (!token.token) {
    throw new Error(
      "No access token from Application Default Credentials. Run " +
        "`gcloud auth application-default login` in this environment.",
    );
  }
  return token.token;
}

/** `projects/P/locations/L/reasoningEngines/E` -> `L`. */
export function extractLocation(resourceName: string): string {
  const match = resourceName.match(/\/locations\/([^/]+)\//);
  if (!match) {
    throw new Error(
      `COUNTERSIGN_CASE_ENGINE_URL is not a reasoningEngines resource name: ${resourceName}`,
    );
  }
  return match[1];
}

function baseUrl(resourceName: string): string {
  const location = extractLocation(resourceName);
  return `https://${location}-aiplatform.googleapis.com/${API_VERSION}/${resourceName}`;
}

async function callQuery(
  resourceName: string,
  classMethod: string,
  input: Record<string, unknown>,
): Promise<unknown> {
  const token = await getAccessToken();
  const res = await fetch(`${baseUrl(resourceName)}:query`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ class_method: classMethod, input }),
  });
  const text = await res.text();
  if (!res.ok) {
    throw new EngineCallError(classMethod, res.status, text);
  }
  if (!text) return null;
  const parsed = JSON.parse(text) as unknown;
  // `:query` wraps every result in an `output` envelope, unlike
  // `:streamQuery` which emits bare event objects. Verified live against the
  // deployed engine: async_create_session returns
  // {"output":{"id":"...","events":[],"app_name":"app",...}}.
  if (
    parsed &&
    typeof parsed === "object" &&
    "output" in (parsed as Record<string, unknown>)
  ) {
    return (parsed as { output: unknown }).output;
  }
  return parsed;
}

/**
 * Normalises a session payload from `:query`.
 *
 * The engine is inconsistent about casing between operations — verified live:
 * `async_create_session` replies with snake_case (`app_name`, `user_id`,
 * `last_update_time`) while `async_get_session` replies with camelCase
 * (`appName`, `userId`, `lastUpdateTime`). `id` and `events` happen to be
 * spelled the same in both, but they are read defensively here so a future
 * casing change surfaces as a clear error rather than an undefined session id
 * silently poisoning every later call.
 */
function normaliseSession(raw: unknown, classMethod: string): EngineSession {
  if (!raw || typeof raw !== "object") {
    throw new Error(`Agent Runtime ${classMethod} returned no session object.`);
  }
  const obj = raw as Record<string, unknown>;
  const id = obj.id ?? obj.sessionId ?? obj.session_id;
  if (typeof id !== "string" || !id) {
    throw new Error(
      `Agent Runtime ${classMethod} returned a session without an id: ` +
        `${JSON.stringify(obj).slice(0, 300)}`,
    );
  }
  const events = Array.isArray(obj.events) ? (obj.events as EngineEvent[]) : [];
  return { id, events };
}

/**
 * Calls `:streamQuery?alt=sse` and returns every event once the stream ends.
 *
 * A single call drains fully — this is not a long-lived SSE hold, it is one
 * bounded HTTP request for one graph step (open a case, or resume one
 * `RequestInput`), which completes well under the platform's 15-minute
 * streaming ceiling. See the module docstring.
 */
async function callStreamQuery(
  resourceName: string,
  classMethod: string,
  input: Record<string, unknown>,
): Promise<EngineEvent[]> {
  const token = await getAccessToken();
  const res = await fetch(`${baseUrl(resourceName)}:streamQuery?alt=sse`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ class_method: classMethod, input }),
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new EngineCallError(classMethod, res.status, text);
  }
  const { readJsonObjectStream } = await import("./json-stream");
  const events: EngineEvent[] = [];
  for await (const obj of readJsonObjectStream(res.body)) {
    events.push(obj as EngineEvent);
  }
  return events;
}

export class EngineCallError extends Error {
  constructor(
    public readonly classMethod: string,
    public readonly status: number,
    public readonly body: string,
  ) {
    super(`Agent Runtime ${classMethod} failed (${status}): ${body.slice(0, 500)}`);
  }
}

export type EngineSession = {
  id: string;
  events: EngineEvent[];
};

export class AgentRuntimeClient {
  constructor(private readonly resourceName: string) {}

  async createSession(userId: string): Promise<EngineSession> {
    const raw = await callQuery(this.resourceName, "async_create_session", {
      user_id: userId,
    });
    return normaliseSession(raw, "async_create_session");
  }

  async getSession(userId: string, sessionId: string): Promise<EngineSession | null> {
    const raw = await callQuery(this.resourceName, "async_get_session", {
      user_id: userId,
      session_id: sessionId,
    });
    if (raw === null || raw === undefined) return null;
    return normaliseSession(raw, "async_get_session");
  }

  /** Starts a new invocation with a structured JSON payload as the message. */
  async start(
    userId: string,
    sessionId: string,
    payload: Record<string, unknown>,
  ): Promise<EngineEvent[]> {
    return callStreamQuery(this.resourceName, "async_stream_query", {
      user_id: userId,
      session_id: sessionId,
      message: {
        role: "user",
        parts: [{ text: JSON.stringify(payload) }],
      },
    });
  }

  /**
   * Resumes a paused `RequestInput` interrupt by replying to its function
   * call. `invocation_id` is deliberately omitted — the Runner resolves it
   * from the matching function call already in the session's event history
   * (`Runner._resolve_invocation_id_from_fr`), which is the documented
   * resume path.
   */
  async resume(
    userId: string,
    sessionId: string,
    interruptId: string,
    functionName: string,
    response: Record<string, unknown>,
  ): Promise<EngineEvent[]> {
    return callStreamQuery(this.resourceName, "async_stream_query", {
      user_id: userId,
      session_id: sessionId,
      message: {
        role: "user",
        parts: [
          {
            function_response: {
              id: interruptId,
              name: functionName,
              response,
            },
          },
        ],
      },
    });
  }
}

export function liveEngineResourceName(): string | null {
  const value = process.env.COUNTERSIGN_CASE_ENGINE_URL?.trim();
  return value ? value : null;
}
