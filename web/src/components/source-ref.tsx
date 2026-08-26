import { labelForSourceUri } from "@/lib/source-uri";

/**
 * Renders a source citation as a human-readable label by default, with the
 * exact raw URI available on demand via the native `<details>` disclosure
 * (no separate client-side state needed). The raw value is never removed
 * from the DOM — it is what a QP or auditor needs, just not the default
 * visible text for a QA reviewer.
 */
export function SourceRef({
  uri,
  className = "font-mono text-[11px] text-ink-faint",
}: {
  uri: string;
  className?: string;
}) {
  return (
    <details className={className}>
      <summary className="cursor-pointer list-none marker:content-none">
        {labelForSourceUri(uri)}
      </summary>
      <span className="mt-0.5 block break-all text-ink-faint">{uri}</span>
    </details>
  );
}

/** Same as `SourceRef`, joined for a list of citations on one line. */
export function SourceRefList({
  uris,
  className = "mt-0.5 block font-mono text-[11px] text-ink-faint",
}: {
  uris: string[];
  className?: string;
}) {
  if (uris.length === 0) return null;
  return (
    <span className={className}>
      {uris.map((uri, index) => (
        <details key={uri} className="inline">
          <summary className="inline cursor-pointer list-none marker:content-none">
            {labelForSourceUri(uri)}
            {index < uris.length - 1 ? " · " : ""}
          </summary>
          <span className="mt-0.5 block break-all">{uri}</span>
        </details>
      ))}
    </span>
  );
}
