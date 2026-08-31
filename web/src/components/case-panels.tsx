import { SourceRef, SourceRefList } from "@/components/source-ref";
import { ItemStatus } from "@/components/status-pill";
import type { CaseDeskRecord } from "@/lib/types";

function Card({
  title,
  children,
  aside,
}: {
  title: string;
  children: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-paper-raised shadow-sm">
      <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
        <h2 className="text-[13px] font-semibold text-ink-soft">{title}</h2>
        {aside && <span className="text-[11px] text-ink-faint">{aside}</span>}
      </header>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

export function CasePanels({ record }: { record: CaseDeskRecord }) {
  const { classified } = record;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <Card title="Intake signals">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
            <Field k="Tenant" v={classified.tenant_id} mono />
            <Field k="Batch" v={classified.batch_id} mono />
            <Field k="Product" v={classified.product_name} />
            <Field k="Doc type" v={classified.doc_type.replaceAll("_", " ")} />
            <Field k="Reported by" v={record.intake.reported_by} />
            <Field k="Detected" v={formatWhen(record.intake.detected_at)} />
            <Field
              k="Patient safety"
              v={yn(record.intake.patient_safety_impact)}
            />
            <Field
              k="Sterility assurance"
              v={yn(record.intake.sterility_assurance_impact)}
            />
            <Field
              k="Quality impact"
              v={record.intake.product_quality_impact}
            />
            <Field
              k="Release criteria"
              v={yn(record.intake.affects_release_criteria)}
            />
            <Field
              k="Reportable"
              v={yn(record.intake.regulatory_reportable)}
            />
            <Field
              k="Prior 90d"
              v={String(record.intake.prior_occurrences_90d)}
            />
          </dl>
          <p className="mt-4 border-t border-line pt-4 text-sm leading-relaxed text-ink-soft">
            {record.intake.description}
          </p>
          <p className="mt-2.5 rounded-lg bg-surface px-3 py-2 font-mono text-[12px] text-ink-soft">
            Rule: {classified.rationale}
          </p>
        </Card>

        {record.extracted_fields.length > 0 && (
          <Card
            title="Extracted fields"
            aside={
              <span className="text-[11px] text-ink-muted">
                Low confidence routes to review
              </span>
            }
          >
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-line">
                  <th className="pb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-soft">Field</th>
                  <th className="pb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-soft">Value</th>
                  <th className="pb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-soft">Confidence</th>
                  <th className="pb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-soft">Route</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {record.extracted_fields.map((f) => (
                  <tr key={f.field_name}>
                    <td className="py-2.5 text-sm text-ink">{f.label}</td>
                    <td className="py-2.5 font-mono text-[13px] font-medium text-ink">{f.value}</td>
                    <td className="tabular py-2.5 font-mono text-[13px] text-ink-soft">
                      {f.confidence.toFixed(2)}
                    </td>
                    <td className="py-2.5">
                      <span className={`text-[12px] font-medium ${f.needs_review ? "text-warn" : "text-minor"}`}>
                        {f.needs_review ? "Needs review" : "Acceptable"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}

        {record.findings.length > 0 && (
          <Card title="Compliance findings">
            <ul className="space-y-3">
              {record.findings.map((f) => (
                <li key={f.finding_id} className="border-b border-line pb-3 last:border-0 last:pb-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-warn-bg px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-warn">
                      {f.finding_type.replaceAll("_", " ")}
                    </span>
                    <span className="text-sm font-medium text-ink">{f.label}</span>
                    <span className="font-mono text-[11px] text-ink-faint">
                      {f.root_cause_category}
                    </span>
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-ink-soft">{f.description}</p>
                  <div className="mt-2 font-mono text-[12px] text-ink-faint">
                    {f.observed_value ?? "—"} vs {f.expected_value ?? "—"}
                    {f.anchor && (
                      <>
                        {" · "}
                        <SourceRef
                          uri={f.anchor.source_uri}
                          className="inline font-mono text-[12px] text-ink-faint"
                        />
                        {` p.${f.anchor.page_number}`}
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        )}

        {record.draft && (
          <Card
            title="Investigation draft"
            aside={
              <span className="font-mono text-[11px] text-ink-muted">
                draft only
              </span>
            }
          >
            {record.draft.hypotheses.map((h) => (
              <div key={h.statement} className="mb-4 last:mb-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full bg-surface px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-ink-soft">
                    {h.category}
                  </span>
                  <span className="font-mono text-[11px] text-ink-faint">
                    confidence {h.confidence.toFixed(2)} · not a determination
                  </span>
                </div>
                <p className="mt-2 text-sm leading-relaxed text-ink">{h.statement}</p>
                <ul className="mt-2.5 space-y-2 border-l-2 border-line pl-3">
                  {h.supporting_claims.map((c) => (
                    <li key={c.claim} className="text-[13px] text-ink-soft">
                      {c.claim}
                      <SourceRefList uris={c.source_uris} />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            {record.draft.product_impact && (
              <p className="mt-3 border-t border-line pt-3 text-sm">
                {record.draft.product_impact.statement}
              </p>
            )}
            {record.draft.open_questions.length > 0 && (
              <ul className="mt-3 list-disc pl-4 text-sm text-ink-muted">
                {record.draft.open_questions.map((q) => (
                  <li key={q}>{q}</li>
                ))}
              </ul>
            )}
          </Card>
        )}

        {record.challenger && record.challenger.attacks.length > 0 && (
          <Card title="Challenger">
            <ul className="space-y-3">
              {record.challenger.attacks.map((a) => (
                <li key={a.detail}>
                  <span className="rounded-full bg-surface px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-ink-soft">
                    {a.attack_type.replaceAll("_", " ")}
                  </span>
                  <p className="mt-1.5 text-sm leading-relaxed text-ink">{a.detail}</p>
                  <p className="mt-0.5 font-mono text-[11px] text-ink-faint">{a.target}</p>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>

      <div className="space-y-4">
        {record.sterility_hold && (
          <Card title="Sterility hold">
            <p className="text-lg font-semibold text-hold">
              {record.sterility_hold.incubation_days}-day incubation
            </p>
            <dl className="mt-3 space-y-1.5">
              <div className="flex justify-between gap-2">
                <dt className="text-[12px] font-medium text-ink-soft">Started</dt>
                <dd className="font-mono text-[12px] text-ink">
                  {formatWhen(record.sterility_hold.started_at)}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-[12px] font-medium text-ink-soft">Due</dt>
                <dd className="font-mono text-[12px] text-ink">
                  {formatWhen(record.sterility_hold.due_at)}
                </dd>
              </div>
            </dl>
            <p className="mt-3 rounded-lg bg-hold-bg px-3 py-2 text-[12px] leading-relaxed text-hold">
              Graph is parked. Resumed by the scheduler, not by a held stream.
            </p>
          </Card>
        )}

        {record.prior_art.length > 0 && (
          <Card
            title="Prior art"
            aside={
              <span className="text-[11px] text-ink-muted">
                {record.prior_art_embedded ? "embedded KNN" : "keyword"}
              </span>
            }
          >
            <ul className="space-y-3">
              {record.prior_art.map((f) => (
                <li key={f.fact_id}>
                  <p className="text-sm leading-relaxed text-ink">{f.fact}</p>
                  <p className="mt-1.5 font-mono text-[11px] text-ink-faint">
                    {f.tenant_id} · {f.batch_id} · {f.confidence.toFixed(2)}
                  </p>
                  <SourceRef uri={f.source_uri} />
                </li>
              ))}
            </ul>
          </Card>
        )}

        {record.branches.length > 0 && (
          <Card title="Departments">
            <ul className="divide-y divide-line">
              {record.branches.map((b) => (
                <li key={b.department} className="flex items-center justify-between gap-2 py-2 first:pt-0 last:pb-0">
                  <span className="text-[13px] capitalize text-ink">
                    {b.department.replaceAll("_", " ")}
                  </span>
                  <span className={`font-mono text-[11px] font-semibold uppercase ${
                    b.status === "ok" ? "text-minor" : "text-warn"
                  }`}>
                    {b.status}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        )}

        {record.release_gate && (
          <Card
            title="Annex 16 §1.7"
            aside={
              <span className="font-mono text-[11px] text-ink-muted">
                v{record.release_gate.spec_version}
              </span>
            }
          >
            {record.release_gate.blocked_reasons.length > 0 && (
              <ul className="mb-4 space-y-1.5 rounded-lg border border-critical/20 bg-critical-bg px-3.5 py-3">
                {record.release_gate.blocked_reasons.map((r) => (
                  <li key={r} className="text-[13px] leading-relaxed text-critical">{r}</li>
                ))}
              </ul>
            )}
            <ul className="max-h-[28rem] divide-y divide-line overflow-auto pr-1">
              {record.release_gate.items.map((item) => (
                <li key={item.annex_ref} className="py-2.5 first:pt-0 last:pb-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[11px] font-semibold text-ink-soft">
                      {item.annex_ref}
                    </span>
                    <ItemStatus value={item.status} />
                  </div>
                  <p className={`mt-0.5 text-[13px] leading-relaxed ${
                    item.required_for_confirmation ? "text-ink" : "text-ink-faint"
                  }`}>
                    {item.title}
                    {item.required_for_confirmation ? "" : " (not scored)"}
                  </p>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}

function Field({
  k,
  v,
  mono,
}: {
  k: string;
  v: string;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-[11px] font-medium uppercase tracking-wider text-ink-faint">
        {k}
      </dt>
      <dd className={mono ? "mt-1 font-mono text-[13px] text-ink" : "mt-1 text-[13px] text-ink"}>
        {v}
      </dd>
    </div>
  );
}

function yn(v: boolean) {
  return v ? "yes" : "no";
}

function formatWhen(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").replace(".000Z", " UTC");
}
