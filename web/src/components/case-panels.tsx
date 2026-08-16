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
    <section className="border border-line bg-paper-raised">
      <header className="flex items-baseline justify-between gap-3 border-b border-line px-4 py-2.5">
        <h2 className="text-[13px] font-semibold tracking-wide uppercase text-ink-muted">
          {title}
        </h2>
        {aside}
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

export function CasePanels({ record }: { record: CaseDeskRecord }) {
  const { classified } = record;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <Card title="Intake signals">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
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
          <p className="mt-3 text-sm text-ink-muted">{record.intake.description}</p>
          <p className="mt-2 font-mono text-[12px] text-ink">
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
            <table className="w-full text-left text-sm">
              <thead className="text-[11px] uppercase tracking-wide text-ink-muted">
                <tr>
                  <th className="py-1 font-medium">Field</th>
                  <th className="py-1 font-medium">Value</th>
                  <th className="py-1 font-medium">Confidence</th>
                  <th className="py-1 font-medium">Route</th>
                </tr>
              </thead>
              <tbody>
                {record.extracted_fields.map((f) => (
                  <tr key={f.field_name} className="border-t border-line">
                    <td className="py-1.5">{f.label}</td>
                    <td className="py-1.5 font-mono text-[13px]">{f.value}</td>
                    <td className="tabular py-1.5 font-mono text-[13px]">
                      {f.confidence.toFixed(2)}
                    </td>
                    <td className="py-1.5 text-[12px]">
                      {f.needs_review ? "Needs review" : "Acceptable"}
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
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-mono text-[11px] uppercase text-ink-muted">
                      {f.finding_type.replaceAll("_", " ")}
                    </span>
                    <span className="text-sm font-medium">{f.label}</span>
                    <span className="font-mono text-[11px] text-ink-muted">
                      {f.root_cause_category}
                    </span>
                  </div>
                  <p className="mt-1 text-sm">{f.description}</p>
                  <p className="mt-1 font-mono text-[12px] text-ink-muted">
                    {f.observed_value ?? "—"} vs {f.expected_value ?? "—"}
                    {f.anchor ? ` · ${f.anchor.source_uri} p.${f.anchor.page_number}` : ""}
                  </p>
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
              <div key={h.statement} className="mb-3 last:mb-0">
                <p className="text-sm">
                  <span className="font-mono text-[11px] uppercase text-ink-muted">
                    {h.category}
                  </span>{" "}
                  {h.statement}
                </p>
                <p className="mt-1 font-mono text-[12px] text-ink-muted">
                  confidence {h.confidence.toFixed(2)} · not a determination
                </p>
                <ul className="mt-2 space-y-1">
                  {h.supporting_claims.map((c) => (
                    <li key={c.claim} className="text-[13px]">
                      <span className="text-ink-muted">Cite · </span>
                      {c.claim}
                      <span className="mt-0.5 block font-mono text-[11px] text-ink-muted">
                        {c.source_uris.join(" · ")}
                      </span>
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
            <ul className="space-y-2 text-sm">
              {record.challenger.attacks.map((a) => (
                <li key={a.detail}>
                  <span className="font-mono text-[11px] uppercase text-ink-muted">
                    {a.attack_type.replaceAll("_", " ")}
                  </span>
                  <p>{a.detail}</p>
                  <p className="font-mono text-[12px] text-ink-muted">{a.target}</p>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>

      <div className="space-y-4">
        {record.sterility_hold && (
          <Card title="Sterility hold">
            <p className="text-sm">
              {record.sterility_hold.incubation_days}-day incubation
            </p>
            <p className="mt-1 font-mono text-[12px] text-ink-muted">
              started {formatWhen(record.sterility_hold.started_at)}
            </p>
            <p className="font-mono text-[12px] text-ink-muted">
              due {formatWhen(record.sterility_hold.due_at)}
            </p>
            <p className="mt-2 text-[13px] text-hold">
              Graph is parked. Do not hold an SSE stream across this wait.
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
                  <p className="text-sm">{f.fact}</p>
                  <p className="mt-1 font-mono text-[11px] text-ink-muted">
                    {f.tenant_id} · {f.batch_id} · {f.confidence.toFixed(2)}
                  </p>
                  <p className="font-mono text-[11px] text-ink-muted">{f.source_uri}</p>
                </li>
              ))}
            </ul>
          </Card>
        )}

        {record.branches.length > 0 && (
          <Card title="Departments">
            <ul className="space-y-2 text-sm">
              {record.branches.map((b) => (
                <li key={b.department} className="flex justify-between gap-2">
                  <span className="capitalize">{b.department}</span>
                  <span className="font-mono text-[12px] uppercase text-ink-muted">
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
              <ul className="mb-3 space-y-1 text-[13px] text-critical">
                {record.release_gate.blocked_reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            )}
            <ul className="max-h-[28rem] space-y-2 overflow-auto pr-1">
              {record.release_gate.items.map((item) => (
                <li key={item.annex_ref} className="text-[13px]">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-mono text-[11px]">{item.annex_ref}</span>
                    <ItemStatus value={item.status} />
                  </div>
                  <p className={item.required_for_confirmation ? "" : "text-ink-muted"}>
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
      <dt className="text-[11px] uppercase tracking-wide text-ink-muted">{k}</dt>
      <dd className={mono ? "font-mono text-[13px]" : "text-sm"}>{v}</dd>
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
