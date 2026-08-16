import type { ChecklistItemStatus, DeviationSeverity, DeskStage, ReleaseGateStatus } from "@/lib/types";

const SEVERITY: Record<DeviationSeverity, string> = {
  critical: "bg-critical/10 text-critical ring-critical/30",
  major: "bg-major/10 text-major ring-major/30",
  minor: "bg-minor/10 text-minor ring-minor/30",
};

const STAGE: Record<DeskStage, string> = {
  classified: "bg-ink/5 text-ink-muted ring-line",
  sterility_hold: "bg-hold/10 text-hold ring-hold/25",
  pending_qp: "bg-teal/10 text-teal-deep ring-teal/25",
  qp_recorded: "bg-minor/10 text-minor ring-minor/25",
};

const GATE: Record<ReleaseGateStatus, string> = {
  ready_for_qp_review: "bg-minor/10 text-minor ring-minor/25",
  not_ready: "bg-warn/10 text-warn ring-warn/30",
};

const ITEM: Record<ChecklistItemStatus, string> = {
  pass: "text-minor",
  fail: "text-critical",
  not_evaluated: "text-ink-muted",
};

function Pill({
  className,
  children,
}: {
  className: string;
  children: string;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-sm px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide ring-1 ${className}`}
    >
      {children}
    </span>
  );
}

export function SeverityPill({ value }: { value: DeviationSeverity }) {
  return <Pill className={SEVERITY[value]}>{value}</Pill>;
}

export function StagePill({ value }: { value: DeskStage }) {
  const label =
    value === "sterility_hold"
      ? "sterility hold"
      : value === "pending_qp"
        ? "pending QP"
        : value === "qp_recorded"
          ? "QP recorded"
          : value;
  return <Pill className={STAGE[value]}>{label}</Pill>;
}

export function GatePill({ value }: { value: ReleaseGateStatus | null }) {
  if (!value) {
    return <Pill className="bg-ink/5 text-ink-muted ring-line">no pack</Pill>;
  }
  return (
    <Pill className={GATE[value]}>
      {value === "ready_for_qp_review" ? "ready for QP" : "not ready"}
    </Pill>
  );
}

export function ItemStatus({ value }: { value: ChecklistItemStatus }) {
  return (
    <span className={`font-mono text-[11px] uppercase ${ITEM[value]}`}>
      {value.replaceAll("_", " ")}
    </span>
  );
}
