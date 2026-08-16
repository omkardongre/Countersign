import type { DeskStage, LifecycleState } from "@/lib/types";

const STEPS: { id: DeskStage | "intake"; label: string }[] = [
  { id: "intake", label: "Detect" },
  { id: "classified", label: "Classify" },
  { id: "sterility_hold", label: "Sterility" },
  { id: "pending_qp", label: "Annex 16" },
  { id: "qp_recorded", label: "QP" },
];

function activeIndex(stage: DeskStage, lifecycle: LifecycleState): number {
  if (stage === "qp_recorded" || lifecycle === "dispositioned") return 4;
  if (stage === "pending_qp") return 3;
  if (stage === "sterility_hold") return 2;
  if (stage === "classified" || lifecycle === "classified") return 1;
  return 0;
}

export function Pipeline({
  stage,
  lifecycle,
}: {
  stage: DeskStage;
  lifecycle: LifecycleState;
}) {
  const active = activeIndex(stage, lifecycle);
  return (
    <ol className="flex flex-wrap gap-0 border border-line bg-paper-raised text-[12px]">
      {STEPS.map((step, i) => {
        const done = i < active;
        const current = i === active;
        return (
          <li
            key={step.id}
            className={`flex flex-1 items-center gap-2 border-line px-3 py-2 ${
              i < STEPS.length - 1 ? "border-r" : ""
            } ${current ? "bg-teal-deep text-paper-raised" : ""} ${
              done ? "text-teal-deep" : current ? "" : "text-ink-muted"
            }`}
          >
            <span className="font-mono tabular text-[11px]">
              {String(i + 1).padStart(2, "0")}
            </span>
            <span>{step.label}</span>
          </li>
        );
      })}
    </ol>
  );
}
