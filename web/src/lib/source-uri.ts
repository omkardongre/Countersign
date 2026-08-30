/**
 * Human-readable labels for internal source URIs shown in the QA-facing UI.
 *
 * The exact URI stays the source of truth in the data model and audit
 * trail (citation-gate integrity depends on it) — this only changes what
 * is shown by default. Callers that need the raw value for audit still
 * have it; see `SourceRef`/`SourceLine` in `@/components/source-ref`.
 */

type SchemeLabeler = (path: string) => string;

/** `path` is everything after `scheme://`. */
const SCHEME_LABELS: Record<string, SchemeLabeler> = {
  "drive": (path) => `Drive document (${lastSegment(path)})`,
  "cloudsql": (path) => `Cloud SQL record (${lastSegment(path)})`,
  "bigquery": (path) => `BigQuery trend data (${lastSegment(path)})`,
  "qms": (path) => `QMS deviation record (${lastSegment(path)})`,
  "case": () => "Case memory reference",
  "google-chat": () => "Google Chat message",
  "google-chat-interview": () => "Google Chat interview record",
  "fixture": (path) => labelFixture(path),
  "http": (path) => labelHttp(path),
  "https": (path) => labelHttp(path),
};

/** SENAITE's source_uri is a real https?:// URL into its JSON API, not a
 * custom scheme. Detect it by path shape so it still gets a human label
 * instead of showing the raw API query string. */
function labelHttp(path: string): string {
  if (path.includes("@@API/senaite/")) return "SENAITE LIMS record";
  if (path.includes("/app/countersign-coa-verification/")) return "ERPNext supply record";
  if (path.includes("/app/stock-entry/")) return "ERPNext manufacture stock entry";
  return `${path.split("/")[0] ?? path} record`;
}

function lastSegment(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

function labelFixture(path: string): string {
  if (path.startsWith("calendar/")) return "Demo calendar event (fixture)";
  if (path.startsWith("drive/")) return "Demo drive document (fixture)";
  if (path.startsWith("chat/")) return "Demo chat thread (fixture)";
  if (path.startsWith("users/")) return "Demo user reference (fixture)";
  return "Demo data (fixture)";
}

/** Human-readable label for one source URI; falls back to the raw value
 * for an unrecognized or non-URI string, so nothing renders empty. */
export function labelForSourceUri(uri: string): string {
  const separatorIndex = uri.indexOf("://");
  if (separatorIndex === -1) return uri;
  const scheme = uri.slice(0, separatorIndex);
  const path = uri.slice(separatorIndex + 3);
  const labeler = SCHEME_LABELS[scheme];
  return labeler ? labeler(path) : uri;
}
