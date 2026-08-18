/**
 * Incremental parser for a byte stream of back-to-back JSON objects.
 *
 * Agent Runtime's `:streamQuery?alt=sse` endpoint does not emit RFC 8895
 * `data: ` framing (verified against `infra/geap/evidence/stream_allowed.sse`
 * — three JSON objects, no `data:` prefix, no blank-line separators). Each
 * top-level `{...}` is one ADK Event, concatenated directly. This scans a
 * growing text buffer and yields each complete top-level object as soon as
 * its closing brace arrives, so a caller can react to `RequestInput`
 * interrupts without waiting for the whole response to finish.
 */

export class JsonObjectStreamParser {
  private buffer = "";

  /** Feed newly-received text. Returns any objects newly completed. */
  push(chunk: string): unknown[] {
    this.buffer += chunk;
    const found: unknown[] = [];
    for (;;) {
      const extracted = this.extractOne();
      if (extracted === null) break;
      found.push(extracted.value);
      this.buffer = this.buffer.slice(extracted.consumed);
    }
    return found;
  }

  /** Call after the stream ends; throws if a partial object remains. */
  finish(): void {
    if (this.buffer.trim().length > 0) {
      throw new Error(
        `Agent Runtime stream ended mid-object: ${this.buffer.slice(0, 200)}`,
      );
    }
  }

  private extractOne(): { value: unknown; consumed: number } | null {
    let i = 0;
    const n = this.buffer.length;
    while (i < n && /\s/.test(this.buffer[i])) i++;
    if (i >= n || this.buffer[i] !== "{") return null;

    let depth = 0;
    let inString = false;
    let escaped = false;
    const start = i;
    for (; i < n; i++) {
      const ch = this.buffer[i];
      if (inString) {
        if (escaped) {
          escaped = false;
        } else if (ch === "\\") {
          escaped = true;
        } else if (ch === '"') {
          inString = false;
        }
        continue;
      }
      if (ch === '"') {
        inString = true;
        continue;
      }
      if (ch === "{") depth++;
      else if (ch === "}") {
        depth--;
        if (depth === 0) {
          const text = this.buffer.slice(start, i + 1);
          return { value: JSON.parse(text), consumed: i + 1 };
        }
      }
    }
    return null; // Object not yet complete; wait for more chunks.
  }
}

/** Reads a fetch Response body to completion, yielding parsed JSON objects. */
export async function* readJsonObjectStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<unknown, void, unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new JsonObjectStreamParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      const objects = parser.push(decoder.decode(value, { stream: true }));
      for (const obj of objects) yield obj;
    }
    const tail = parser.push(decoder.decode());
    for (const obj of tail) yield obj;
    parser.finish();
  } finally {
    reader.releaseLock();
  }
}
