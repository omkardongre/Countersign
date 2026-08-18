"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { classifySeverity } from "@/lib/classify";
import type { CaseIntake, DocType, ProductQualityImpact } from "@/lib/types";

const DOCS: { value: DocType; label: string }[] = [
  { value: "batch_record", label: "Batch record" },
  { value: "lab_result", label: "Lab result" },
  { value: "supplier_coa", label: "Supplier CoA" },
  { value: "operator_report", label: "Operator report" },
  { value: "environmental_log", label: "Environmental log" },
];

export function OpenForm({ liveEngine }: { liveEngine: boolean }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [useLive, setUseLive] = useState(liveEngine);
  const [form, setForm] = useState({
    tenant_id: "aurex",
    batch_id: "",
    product_name: "",
    doc_type: "batch_record" as DocType,
    description: "",
    reported_by: "",
    evidence_refs: "",
    patient_safety_impact: false,
    sterility_assurance_impact: false,
    product_quality_impact: "none" as ProductQualityImpact,
    affects_release_criteria: false,
    regulatory_reportable: false,
    prior_occurrences_90d: 0,
  });

  const preview = useMemo(() => {
    const intake = toIntake(form);
    return classifySeverity(intake);
  }, [form]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const res = await fetch("/api/cases", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          source: useLive ? "live" : "intake",
          intake: toIntake(form),
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        setError(body.error ?? "Could not open case.");
        return;
      }
      router.push(`/cases/${body.classified.case_id}`);
      router.refresh();
    } catch {
      setError("Network error.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="max-w-2xl space-y-5">
      <p className="text-sm text-ink-muted">
        Structured intake only. Classification is the same deterministic rule
        as <span className="font-mono">app/domain/classification.py</span>.
      </p>
      <fieldset className="border border-line bg-paper-raised px-4 py-3">
        <legend className="px-1 text-[12px] uppercase text-ink-muted">
          Execution
        </legend>
        <label htmlFor="use_live" className="flex items-start gap-2 text-sm">
          <input
            id="use_live"
            type="checkbox"
            checked={useLive}
            disabled={!liveEngine}
            onChange={(e) => setUseLive(e.target.checked)}
            className="mt-1"
          />
          <span>
            Run on the deployed Agent Runtime
            <span className="mt-0.5 block text-[12px] text-ink-muted">
              {liveEngine
                ? "Starts the real deviation-case graph: departments over MCP, investigation, challenger, Annex 16, QP pause. Desk polls session state."
                : "Unavailable — COUNTERSIGN_CASE_ENGINE_URL is not set. Unchecked opens a local classified case only."}
            </span>
          </span>
        </label>
      </fieldset>
      <div className="grid gap-4 sm:grid-cols-2">
        <Text
          id="tenant_id"
          label="Tenant"
          value={form.tenant_id}
          onChange={(v) => setForm({ ...form, tenant_id: v })}
        />
        <Text
          id="batch_id"
          label="Batch"
          value={form.batch_id}
          onChange={(v) => setForm({ ...form, batch_id: v })}
        />
        <Text
          id="product_name"
          label="Product"
          className="sm:col-span-2"
          value={form.product_name}
          onChange={(v) => setForm({ ...form, product_name: v })}
        />
        <div>
          <label htmlFor="doc_type" className="block text-[12px] uppercase text-ink-muted">
            Document type
          </label>
          <select
            id="doc_type"
            value={form.doc_type}
            onChange={(e) =>
              setForm({ ...form, doc_type: e.target.value as DocType })
            }
            className="mt-1 w-full border border-line bg-paper-raised px-3 py-2 text-sm"
          >
            {DOCS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </div>
        <Text
          id="reported_by"
          label="Reported by"
          value={form.reported_by}
          onChange={(v) => setForm({ ...form, reported_by: v })}
        />
      </div>
      <div>
        <label htmlFor="description" className="block text-[12px] uppercase text-ink-muted">
          Description
        </label>
        <textarea
          id="description"
          required
          rows={3}
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
          className="mt-1 w-full border border-line bg-paper-raised px-3 py-2 text-sm"
        />
        <p className="mt-1 text-[12px] text-ink-muted">
          Human summary. Not parsed for classification.
        </p>
      </div>
      <Text
        id="evidence_refs"
        label="Evidence refs"
        value={form.evidence_refs}
        onChange={(v) => setForm({ ...form, evidence_refs: v })}
      />
      <fieldset className="space-y-2 border border-line bg-paper-raised px-4 py-3">
        <legend className="px-1 text-[12px] uppercase text-ink-muted">
          Classification signals
        </legend>
        <Check
          label="Patient safety impact"
          checked={form.patient_safety_impact}
          onChange={(v) => setForm({ ...form, patient_safety_impact: v })}
        />
        <Check
          label="Sterility assurance impact"
          checked={form.sterility_assurance_impact}
          onChange={(v) => setForm({ ...form, sterility_assurance_impact: v })}
        />
        <Check
          label="Affects release criteria"
          checked={form.affects_release_criteria}
          onChange={(v) => setForm({ ...form, affects_release_criteria: v })}
        />
        <Check
          label="Regulatory reportable"
          checked={form.regulatory_reportable}
          onChange={(v) => setForm({ ...form, regulatory_reportable: v })}
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="pqi" className="block text-[12px] uppercase text-ink-muted">
              Product quality impact
            </label>
            <select
              id="pqi"
              value={form.product_quality_impact}
              onChange={(e) =>
                setForm({
                  ...form,
                  product_quality_impact: e.target.value as ProductQualityImpact,
                })
              }
              className="mt-1 w-full border border-line bg-paper px-3 py-2 text-sm"
            >
              <option value="none">none</option>
              <option value="potential">potential</option>
              <option value="confirmed">confirmed</option>
            </select>
          </div>
          <div>
            <label htmlFor="prior" className="block text-[12px] uppercase text-ink-muted">
              Prior occurrences (90d)
            </label>
            <input
              id="prior"
              type="number"
              min={0}
              value={form.prior_occurrences_90d}
              onChange={(e) =>
                setForm({
                  ...form,
                  prior_occurrences_90d: Number(e.target.value),
                })
              }
              className="mt-1 w-full border border-line bg-paper px-3 py-2 text-sm"
            />
          </div>
        </div>
      </fieldset>
      <p className="border border-line bg-paper-raised px-4 py-3 text-sm">
        Preview severity{" "}
        <span className="font-mono uppercase">{preview.severity}</span>
        <span className="mt-1 block font-mono text-[12px] text-ink-muted">
          {preview.rationale}
        </span>
      </p>
      {error && <p className="text-sm text-critical">{error}</p>}
      <button
        type="submit"
        disabled={pending}
        className="bg-teal-deep px-4 py-2 text-sm text-paper-raised disabled:opacity-50"
      >
        {pending
          ? useLive
            ? "Running graph on Agent Runtime…"
            : "Opening…"
          : useLive
            ? "Open case on Agent Runtime"
            : "Open classified case"}
      </button>
      {useLive && (
        <p className="text-[12px] text-ink-muted">
          First run reaches the QP pause or the sterility hold, whichever comes
          first. This may take up to a minute.
        </p>
      )}
    </form>
  );
}

function toIntake(form: {
  tenant_id: string;
  batch_id: string;
  product_name: string;
  doc_type: DocType;
  description: string;
  reported_by: string;
  evidence_refs: string;
  patient_safety_impact: boolean;
  sterility_assurance_impact: boolean;
  product_quality_impact: ProductQualityImpact;
  affects_release_criteria: boolean;
  regulatory_reportable: boolean;
  prior_occurrences_90d: number;
}): CaseIntake {
  return {
    tenant_id: form.tenant_id.trim(),
    batch_id: form.batch_id.trim(),
    product_name: form.product_name.trim(),
    doc_type: form.doc_type,
    description: form.description.trim(),
    reported_by: form.reported_by.trim(),
    evidence_refs: form.evidence_refs
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter(Boolean),
    detected_at: new Date().toISOString(),
    patient_safety_impact: form.patient_safety_impact,
    sterility_assurance_impact: form.sterility_assurance_impact,
    product_quality_impact: form.product_quality_impact,
    affects_release_criteria: form.affects_release_criteria,
    regulatory_reportable: form.regulatory_reportable,
    prior_occurrences_90d: form.prior_occurrences_90d,
  };
}

function Text({
  id,
  label,
  value,
  onChange,
  className = "",
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  className?: string;
}) {
  return (
    <div className={className}>
      <label htmlFor={id} className="block text-[12px] uppercase text-ink-muted">
        {label}
      </label>
      <input
        id={id}
        required={id !== "evidence_refs"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full border border-line bg-paper-raised px-3 py-2 text-sm"
      />
    </div>
  );
}

function Check({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  const id = label.replaceAll(" ", "-").toLowerCase();
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-sm">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}
