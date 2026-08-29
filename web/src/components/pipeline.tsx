import type { DeskStage, LifecycleState } from "@/lib/types";

const STEPS: { id: DeskStage | "intake"; label: string; description: string }[] = [
  { id: "intake",          label: "Detect",    description: "Deviation recorded" },
  { id: "classified",      label: "Classify",  description: "Severity & impact" },
  { id: "sterility_hold",  label: "Sterility", description: "QA-set incubation hold" },
  { id: "pending_qp",      label: "Annex 16",  description: "QP review pack" },
  { id: "qp_recorded",     label: "QP",        description: "Disposition recorded" },
];

function activeIndex(stage: DeskStage, lifecycle: LifecycleState): number {
  if (stage === "qp_recorded" || lifecycle === "dispositioned") return 4;
  if (stage === "pending_qp")    return 3;
  if (stage === "sterility_hold") return 2;
  if (stage === "classified" || lifecycle === "classified") return 1;
  return 0;
}

export function Pipeline({ stage, lifecycle }: { stage: DeskStage; lifecycle: LifecycleState }) {
  const active = activeIndex(stage, lifecycle);

  return (
    <div className="rounded-xl border border-line bg-paper-raised p-4 shadow-sm">
      <ol className="flex items-center">
        {STEPS.map((step, i) => {
          const done    = i < active;
          const current = i === active;
          const last    = i === STEPS.length - 1;

          return (
            <li key={step.id} className="flex flex-1 items-center">
              {/* Step node */}
              <div className="flex flex-col items-center">
                <div
                  className={`flex h-8 w-8 items-center justify-center rounded-full text-[12px] font-bold ring-2 transition-all ${
                    done
                      ? "bg-brand ring-brand text-white"
                      : current
                        ? "bg-brand ring-brand ring-offset-2 ring-offset-paper-raised text-white"
                        : "bg-surface ring-line text-ink-faint"
                  }`}
                >
                  {done ? (
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    <span>{i + 1}</span>
                  )}
                </div>
                <div className="mt-2 text-center">
                  <p className={`text-[12px] font-semibold ${current ? "text-brand" : done ? "text-ink" : "text-ink-faint"}`}>
                    {step.label}
                  </p>
                  <p className="text-[10px] text-ink-faint">{step.description}</p>
                </div>
              </div>

              {/* Connector line */}
              {!last && (
                <div className={`mx-1 mb-7 h-0.5 flex-1 ${i < active ? "bg-brand" : "bg-line"}`} />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
