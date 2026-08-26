"use client";

import { useRouter } from "next/navigation";
import type { KeyboardEvent, MouseEvent } from "react";
import { GatePill, SeverityPill, StagePill } from "@/components/status-pill";
import type { CaseQueueItem } from "@/lib/types";

export function QueueRow({ c }: { c: CaseQueueItem }) {
  const router = useRouter();
  const href = `/cases/${c.case_id}`;

  function go(event: MouseEvent | KeyboardEvent) {
    if ("button" in event && event.button !== 0) return;
    router.push(href);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTableRowElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      go(event);
    }
  }

  return (
    <tr
      role="link"
      tabIndex={0}
      aria-label={`Open case ${c.batch_id}`}
      onClick={go}
      onKeyDown={onKeyDown}
      className="group cursor-pointer transition-colors hover:bg-surface/70 focus-visible:bg-surface/70"
    >
      <td className="px-5 py-4 font-mono text-[12px] text-ink-muted">
        {c.tenant_id}
      </td>
      <td className="px-5 py-4">
        <span className="font-mono text-[13px] font-semibold text-brand underline-offset-2 group-hover:underline">
          {c.batch_id}
        </span>
      </td>
      <td className="px-5 py-4 text-[13px] text-ink-soft">{c.product_name}</td>
      <td className="px-5 py-4"><SeverityPill value={c.severity} /></td>
      <td className="px-5 py-4"><StagePill value={c.desk_stage} /></td>
      <td className="px-5 py-4"><GatePill value={c.gate_status} /></td>
      <td className="px-5 py-4">
        <span
          className={`font-mono text-[11px] uppercase ${
            c.origin === "live" ? "font-semibold text-brand" : "text-ink-faint"
          }`}
        >
          {c.origin === "live" ? "live" : c.origin}
        </span>
      </td>
      <td className="pr-4 text-right text-ink-faint opacity-0 transition-opacity group-hover:opacity-100">
        <svg className="inline h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
        </svg>
      </td>
    </tr>
  );
}
