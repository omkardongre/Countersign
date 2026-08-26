"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { classifySeverity } from "@/lib/classify";
import { openDrivePicker } from "@/lib/drive-picker";
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
  const [driveFileId, setDriveFileId] = useState("");
  const [driveFileName, setDriveFileName] = useState("");
  const [pickerError, setPickerError] = useState<string | null>(null);
  const [pickerBusy, setPickerBusy] = useState(false);

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
    sterility_hold_days: "" as string,
    product_quality_impact: "none" as ProductQualityImpact,
    affects_release_criteria: false,
    regulatory_reportable: false,
    prior_occurrences_90d: 0,
  });

  const preview = useMemo(() => classifySeverity(toIntake(form)), [form]);

  async function onPickDriveFile() {
    setPickerError(null);
    setPickerBusy(true);
    try {
      const picked = await openDrivePicker();
      if (picked) {
        setDriveFileId(picked.id);
        setDriveFileName(picked.name);
      }
    } catch (err) {
      setPickerError(err instanceof Error ? err.message : "Could not open Drive picker.");
    } finally {
      setPickerBusy(false);
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const holdDays = Number.parseInt(form.sterility_hold_days, 10);
    if (!Number.isFinite(holdDays) || holdDays <= 0) {
      setError(
        "Sterility incubation hold (days) is required. There is no platform default.",
      );
      return;
    }
    setPending(true);
    try {
      const res = await fetch("/api/cases", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          source: useLive ? "live" : "intake",
          intake: toIntake(form),
          ...(useLive ? { drive_file_id: driveFileId.trim() } : {}),
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

  const severityTone =
    preview.severity === "critical" ? "text-critical bg-critical-bg" :
    preview.severity === "major" ? "text-major bg-major-bg" :
    "text-minor bg-minor-bg";

  return (
    <form onSubmit={onSubmit} className="space-y-5">
      {/* Execution mode */}
      <Section
        title="Execution"
        description="Where this case runs. Live mode invokes the deployed graph on Agent Runtime."
      >
        <label
          htmlFor="use_live"
          className={`flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition-colors ${
            useLive ? "border-brand bg-brand-soft" : "border-line hover:border-line-strong"
          } ${!liveEngine ? "cursor-not-allowed opacity-60" : ""}`}
        >
          <input
            id="use_live"
            type="checkbox"
            checked={useLive}
            disabled={!liveEngine}
            onChange={(e) => setUseLive(e.target.checked)}
            className="mt-0.5 h-4 w-4 accent-[var(--color-brand)]"
          />
          <span>
            <span className="block text-sm font-semibold text-ink">
              Run on the deployed Agent Runtime
            </span>
            <span className="mt-1 block text-[13px] leading-relaxed text-ink-muted">
              {liveEngine
                ? "Starts the real deviation-case graph: departments over MCP, investigation, challenger, Annex 16, then the QP pause."
                : "Unavailable — COUNTERSIGN_CASE_ENGINE_URL is not set. Unchecked opens a local classified case only."}
            </span>
          </span>
        </label>

        {useLive && (
          <div className="mt-4 rounded-lg border border-line bg-surface/50 p-4">
            <p className="label">Google Drive evidence file</p>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={onPickDriveFile}
                disabled={pickerBusy}
                className="rounded-lg border border-line-strong bg-paper-raised px-3.5 py-2 text-[13px] font-medium text-ink shadow-sm transition-colors hover:bg-surface disabled:opacity-50"
              >
                {pickerBusy ? "Opening picker…" : driveFileId ? "Choose a different file" : "Choose from Drive"}
              </button>
              {driveFileName && (
                <span className="truncate text-[13px] text-ink-soft" title={driveFileName}>
                  {driveFileName}
                </span>
              )}
            </div>
            <input
              id="drive_file_id"
              required
              value={driveFileId}
              onChange={() => {}}
              className="sr-only"
              tabIndex={-1}
              aria-hidden="true"
            />
            {pickerError && <p className="mt-2 text-[13px] text-critical">{pickerError}</p>}
            <p className="mt-2 text-[12px] leading-relaxed text-ink-faint">
              Required. The manufacturing branch pulls this exact file from Drive,
              so it must be shared read-only with the department MCP service account.
            </p>
          </div>
        )}
      </Section>

      {/* Batch identity */}
      <Section title="Batch identity">
        <div className="grid gap-4 sm:grid-cols-2">
          <Text id="tenant_id" label="Tenant" value={form.tenant_id} onChange={(v) => setForm({ ...form, tenant_id: v })} />
          <Text id="batch_id" label="Batch" placeholder="AX-2291" value={form.batch_id} onChange={(v) => setForm({ ...form, batch_id: v })} />
          <Text id="product_name" label="Product" className="sm:col-span-2" placeholder="AXR450 Sterile Injectable" value={form.product_name} onChange={(v) => setForm({ ...form, product_name: v })} />
          <div>
            <label htmlFor="doc_type" className="label">Document type</label>
            <select
              id="doc_type"
              value={form.doc_type}
              onChange={(e) => setForm({ ...form, doc_type: e.target.value as DocType })}
              className="field"
            >
              {DOCS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
            </select>
          </div>
          <Text id="reported_by" label="Reported by" placeholder="M. Chen" value={form.reported_by} onChange={(v) => setForm({ ...form, reported_by: v })} />
        </div>
      </Section>

      {/* Deviation detail */}
      <Section title="Deviation detail">
        <div>
          <label htmlFor="description" className="label">Description</label>
          <textarea
            id="description"
            required
            rows={3}
            placeholder="What was observed, where, and when."
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            className="field resize-y"
          />
          <p className="mt-1.5 text-[12px] text-ink-faint">
            Human summary. Not parsed for classification.
          </p>
        </div>
        <div className="mt-4">
          <Text
            id="evidence_refs"
            label="Evidence references"
            placeholder="drive://... (space or comma separated)"
            value={form.evidence_refs}
            onChange={(v) => setForm({ ...form, evidence_refs: v })}
          />
        </div>
      </Section>

      {/* Classification signals */}
      <Section
        title="Classification signals"
        description="These fields drive the deterministic severity rule."
      >
        <div className="grid gap-2.5 sm:grid-cols-2">
          <Check label="Patient safety impact" checked={form.patient_safety_impact} onChange={(v) => setForm({ ...form, patient_safety_impact: v })} />
          <Check label="Sterility assurance impact" checked={form.sterility_assurance_impact} onChange={(v) => setForm({ ...form, sterility_assurance_impact: v })} />
          <Check label="Affects release criteria" checked={form.affects_release_criteria} onChange={(v) => setForm({ ...form, affects_release_criteria: v })} />
          <Check label="Regulatory reportable" checked={form.regulatory_reportable} onChange={(v) => setForm({ ...form, regulatory_reportable: v })} />
        </div>

        <div className="mt-4">
          <label htmlFor="sterility-hold-days" className="label">
            Sterility incubation hold (days)
          </label>
          <input
            id="sterility-hold-days"
            type="number"
            min={1}
            required
            placeholder="e.g. 14"
            value={form.sterility_hold_days}
            onChange={(e) => setForm({ ...form, sterility_hold_days: e.target.value })}
            className="field tabular sm:w-48"
          />
          <p className="mt-1 text-[12px] text-ink-muted">
            Required. Product-specific pharmacopeia sterility test duration
            for this batch. There is no platform default — QA sets the exact
            hold here for every case.
          </p>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="pqi" className="label">Product quality impact</label>
            <select
              id="pqi"
              value={form.product_quality_impact}
              onChange={(e) => setForm({ ...form, product_quality_impact: e.target.value as ProductQualityImpact })}
              className="field"
            >
              <option value="none">None</option>
              <option value="potential">Potential</option>
              <option value="confirmed">Confirmed</option>
            </select>
          </div>
          <div>
            <label htmlFor="prior" className="label">Prior occurrences (90 days)</label>
            <input
              id="prior"
              type="number"
              min={0}
              value={form.prior_occurrences_90d}
              onChange={(e) => setForm({ ...form, prior_occurrences_90d: Number(e.target.value) })}
              className="field tabular"
            />
          </div>
        </div>
      </Section>

      {/* Severity preview */}
      <div className="rounded-xl border border-line bg-paper-raised p-5 shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-[12px] font-semibold uppercase tracking-wider text-ink-muted">
            Computed severity
          </p>
          <span className={`rounded-full px-2.5 py-0.5 font-mono text-[11px] font-semibold uppercase ${severityTone}`}>
            {preview.severity}
          </span>
        </div>
        <p className="mt-2 font-mono text-[12px] leading-relaxed text-ink-muted">
          {preview.rationale}
        </p>
      </div>

      {error && (
        <p className="rounded-lg border border-critical/20 bg-critical-bg px-4 py-3 text-sm text-critical">
          {error}
        </p>
      )}

      {/* Submit */}
      <div className="flex flex-wrap items-center gap-4 pt-1">
        <button
          type="submit"
          disabled={pending}
          className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-dark disabled:opacity-50"
        >
          {pending && (
            <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          )}
          {pending
            ? useLive ? "Running on Agent Runtime…" : "Opening…"
            : useLive ? "Open case on Agent Runtime" : "Open classified case"}
        </button>
        {useLive && (
          <p className="text-[12px] text-ink-faint">
            First run reaches the QP pause or the sterility hold. May take up to a minute.
          </p>
        )}
      </div>
    </form>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-line bg-paper-raised p-5 shadow-sm">
      <div className="mb-4">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {description && (
          <p className="mt-0.5 text-[13px] text-ink-muted">{description}</p>
        )}
      </div>
      {children}
    </section>
  );
}

function Text({
  id,
  label,
  value,
  onChange,
  className = "",
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  className?: string;
  placeholder?: string;
}) {
  return (
    <div className={className}>
      <label htmlFor={id} className="label">{label}</label>
      <input
        id={id}
        required={id !== "evidence_refs"}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="field"
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
    <label
      htmlFor={id}
      className={`flex cursor-pointer items-center gap-2.5 rounded-lg border px-3.5 py-2.5 text-[13px] transition-colors ${
        checked ? "border-brand bg-brand-soft font-medium text-ink" : "border-line text-ink-soft hover:border-line-strong"
      }`}
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-[var(--color-brand)]"
      />
      {label}
    </label>
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
  sterility_hold_days: string;
  product_quality_impact: ProductQualityImpact;
  affects_release_criteria: boolean;
  regulatory_reportable: boolean;
  prior_occurrences_90d: number;
}): CaseIntake {
  const parsedHoldDays = Number.parseInt(form.sterility_hold_days, 10);
  return {
    tenant_id: form.tenant_id.trim(),
    batch_id: form.batch_id.trim(),
    product_name: form.product_name.trim(),
    doc_type: form.doc_type,
    description: form.description.trim(),
    reported_by: form.reported_by.trim(),
    evidence_refs: form.evidence_refs.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean),
    detected_at: new Date().toISOString(),
    patient_safety_impact: form.patient_safety_impact,
    sterility_assurance_impact: form.sterility_assurance_impact,
    // Required; onSubmit blocks the request before this reaches the server
    // if it is not a positive integer. The severity preview above tolerates
    // an incomplete in-progress value (defaults to 1) since it never submits.
    sterility_hold_days:
      Number.isFinite(parsedHoldDays) && parsedHoldDays > 0 ? parsedHoldDays : 1,
    product_quality_impact: form.product_quality_impact,
    affects_release_criteria: form.affects_release_criteria,
    regulatory_reportable: form.regulatory_reportable,
    prior_occurrences_90d: form.prior_occurrences_90d,
  };
}
