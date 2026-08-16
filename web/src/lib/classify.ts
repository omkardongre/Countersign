import type { CaseIntake, DeviationSeverity } from "./types";

const RECURRENCE_ESCALATION_THRESHOLD = 3;

export function classifySeverity(
  intake: CaseIntake,
): { severity: DeviationSeverity; rationale: string } {
  if (intake.patient_safety_impact) {
    return { severity: "critical", rationale: "patient_safety_impact=true" };
  }
  if (intake.sterility_assurance_impact) {
    return { severity: "critical", rationale: "sterility_assurance_impact=true" };
  }
  if (intake.product_quality_impact === "confirmed") {
    return { severity: "critical", rationale: "product_quality_impact=confirmed" };
  }
  if (intake.regulatory_reportable) {
    return { severity: "critical", rationale: "regulatory_reportable=true" };
  }
  if (intake.affects_release_criteria) {
    return { severity: "major", rationale: "affects_release_criteria=true" };
  }
  if (intake.product_quality_impact === "potential") {
    return { severity: "major", rationale: "product_quality_impact=potential" };
  }
  if (intake.prior_occurrences_90d >= RECURRENCE_ESCALATION_THRESHOLD) {
    return {
      severity: "major",
      rationale: `prior_occurrences_90d>=${RECURRENCE_ESCALATION_THRESHOLD} (recurring, systemic)`,
    };
  }
  return {
    severity: "minor",
    rationale: "no critical or major trigger matched",
  };
}
