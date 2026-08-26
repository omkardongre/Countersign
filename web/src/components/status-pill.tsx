import type { ChecklistItemStatus, DeviationSeverity, DeskStage, ReleaseGateStatus } from "@/lib/types";

function Pill({ className, children }: { className: string; children: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wide ${className}`}>
      {children}
    </span>
  );
}

export function SeverityPill({ value }: { value: DeviationSeverity }) {
  const cls =
    value === "critical" ? "bg-critical-bg text-critical ring-1 ring-critical/20" :
    value === "major"    ? "bg-major-bg text-major ring-1 ring-major/20" :
                           "bg-minor-bg text-minor ring-1 ring-minor/20";
  return <Pill className={cls}>{value}</Pill>;
}

export function StagePill({ value }: { value: DeskStage }) {
  const label =
    value === "sterility_hold" ? "sterility hold" :
    value === "pending_qp"     ? "pending QP" :
    value === "qp_recorded"    ? "QP recorded" : value;

  const cls =
    value === "pending_qp"     ? "bg-brand/10 text-brand ring-1 ring-brand/20" :
    value === "sterility_hold" ? "bg-hold-bg text-hold ring-1 ring-hold/20" :
    value === "qp_recorded"    ? "bg-minor-bg text-minor ring-1 ring-minor/20" :
                                 "bg-surface text-ink-muted ring-1 ring-line";
  return <Pill className={cls}>{label}</Pill>;
}

export function GatePill({ value }: { value: ReleaseGateStatus | null }) {
  if (!value) return <Pill className="bg-surface text-ink-faint ring-1 ring-line">no pack</Pill>;
  const cls =
    value === "ready_for_qp_review"
      ? "bg-minor-bg text-minor ring-1 ring-minor/20"
      : "bg-warn-bg text-warn ring-1 ring-warn/20";
  return <Pill className={cls}>{value === "ready_for_qp_review" ? "ready for QP" : "not ready"}</Pill>;
}

export function ItemStatus({ value }: { value: ChecklistItemStatus }) {
  const cls =
    value === "pass"          ? "text-minor font-semibold" :
    value === "fail"          ? "text-critical font-semibold" :
                                "text-ink-faint";
  return (
    <span className={`font-mono text-[11px] uppercase ${cls}`}>
      {value.replaceAll("_", " ")}
    </span>
  );
}
