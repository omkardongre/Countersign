import type {
  CaseDeskRecord,
  CfiDeskState,
  CfiInterviewSummary,
  CfiOutcome,
} from "@/lib/types";

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
        <h3 className="text-[13px] font-semibold tracking-wide uppercase text-ink-muted">
          {title}
        </h3>
        {aside}
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

export function CfiPanel({ record }: { record: CaseDeskRecord }) {
  const cfi = record.cfi;
  if (!cfi) return null;

  return (
    <section className="border border-teal-deep bg-paper-raised">
      <header className="border-b border-line px-4 py-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h2 className="text-[13px] font-semibold tracking-wide uppercase text-teal-deep">
              Cross-functional investigation
            </h2>
            <p className="mt-1 text-[13px] text-ink-muted">
              Separate durable investigation record. It does not release a
              batch, change the Annex 16 gate, or replace QP confirmation.
            </p>
          </div>
          <CfiStatus value={cfi.outcome} launchStatus={cfi.launch_status} />
        </div>
      </header>
      <div className="grid gap-4 p-4 lg:grid-cols-2">
        <CfiIdentity cfi={cfi} />
        <CfiBudget cfi={cfi} />
        <CfiHypotheses cfi={cfi} />
        <CfiInterviews interviews={cfi.interviews} />
        <CfiEvidence cfi={cfi} />
        <CfiArtifacts cfi={cfi} />
        <div className="lg:col-span-2">
          <CfiActivity cfi={cfi} />
        </div>
      </div>
    </section>
  );
}

function CfiIdentity({ cfi }: { cfi: CfiDeskState }) {
  return (
    <Card title="CFI identity">
      <dl className="grid grid-cols-2 gap-x-5 gap-y-2 text-sm">
        <Field k="CFI ID" v={cfi.cfi_id} mono />
        <Field k="Revision" v={String(cfi.revision)} mono />
        <Field k="Source case" v={cfi.source_case_id} mono />
        <Field k="Batch" v={cfi.batch_id} mono />
        <Field k="Opened" v={formatWhen(cfi.opened_at)} />
        <Field k="Closed" v={cfi.closed_at ? formatWhen(cfi.closed_at) : "open"} />
        <Field k="Launch" v={`${cfi.launch_status} · ${cfi.launch_attempts} attempt(s)`} />
        <Field k="Authority" v={cfi.decision_authority} mono />
      </dl>
      <div className="mt-3 border-t border-line pt-3">
        <p className="text-[11px] uppercase tracking-wide text-ink-muted">Trigger reasons</p>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {cfi.trigger_reasons.map((reason) => (
            <span
              key={reason}
              className="rounded-sm bg-paper px-2 py-1 font-mono text-[11px] text-ink"
            >
              {reason}
            </span>
          ))}
        </div>
        {cfi.launch_error && <ErrorText>{cfi.launch_error}</ErrorText>}
      </div>
    </Card>
  );
}

function CfiBudget({ cfi }: { cfi: CfiDeskState }) {
  return (
    <Card
      title="Budget consumed"
      aside={<span className="font-mono text-[11px] text-ink-muted">change-controlled</span>}
    >
      <div className="grid grid-cols-2 gap-3">
        <Metric label="Planning rounds" value={String(cfi.budget.planning_rounds_used)} />
        <Metric
          label="Tool calls this round"
          value={String(cfi.budget.tool_calls_in_current_round)}
        />
      </div>
      <p className="mt-3 font-mono text-[11px] text-ink-muted">
        {cfi.budget.budget_spec_version}
      </p>
      <p className="break-all font-mono text-[11px] text-ink-muted">
        spec sha256 {cfi.budget.budget_spec_sha256}
      </p>
      <p className="mt-2 text-[12px] text-ink-muted">
        Counters are persisted on the CFI aggregate. The panel does not infer
        an unprovided cap.
      </p>
    </Card>
  );
}

function CfiHypotheses({ cfi }: { cfi: CfiDeskState }) {
  return (
    <div className="lg:col-span-2">
      <Card title="Hypothesis ledger" aside={<span className="text-[11px] text-ink-muted">code-settled states</span>}>
        {cfi.hypotheses.length === 0 ? (
          <p className="text-sm text-ink-muted">No hypotheses were opened.</p>
        ) : (
          <ul className="space-y-4">
            {cfi.hypotheses.map((hypothesis) => (
              <li key={hypothesis.hypothesis_id} className="border-b border-line pb-3 last:border-0 last:pb-0">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-mono text-[11px] uppercase text-ink-muted">
                    {hypothesis.hypothesis_id}
                  </span>
                  <span className="font-mono text-[11px] uppercase text-teal-deep">
                    {hypothesis.state}
                  </span>
                  <span className="font-mono text-[11px] text-ink-muted">
                    {hypothesis.category} · round {hypothesis.opened_in_round}
                  </span>
                </div>
                <p className="mt-1 text-sm">{hypothesis.statement}</p>
                <ClaimList label="Supporting" claims={hypothesis.supporting_claims} />
                <ClaimList label="Refuting" claims={hypothesis.refuting_claims} />
                {hypothesis.settled_at && (
                  <p className="mt-2 font-mono text-[11px] text-ink-muted">
                    settled {formatWhen(hypothesis.settled_at)}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function ClaimList({
  label,
  claims,
}: {
  label: string;
  claims: { claim: string; evidence_ids: string[] }[];
}) {
  if (claims.length === 0) return null;
  return (
    <div className="mt-2 border-l-2 border-line pl-3">
      <p className="font-mono text-[11px] uppercase text-ink-muted">{label}</p>
      <ul className="mt-1 space-y-1 text-[13px]">
        {claims.map((claim) => (
          <li key={`${label}-${claim.claim}`}>
            {claim.claim}
            <span className="mt-0.5 block font-mono text-[11px] text-ink-muted">
              evidence: {claim.evidence_ids.join(", ")}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CfiInterviews({ interviews }: { interviews: CfiInterviewSummary[] }) {
  return (
    <Card title="Human interviews" aside={<span className="text-[11px] text-ink-muted">untrusted evidence</span>}>
      {interviews.length === 0 ? (
        <p className="text-sm text-ink-muted">No interview round recorded.</p>
      ) : (
        <ul className="space-y-3">
          {interviews.map((interview) => (
            <li key={interview.interview_id} className="border-b border-line pb-3 last:border-0 last:pb-0">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-mono text-[11px] uppercase text-ink-muted">
                  {interview.status}
                </span>
                <span className="font-mono text-[11px] text-ink-muted">
                  {interview.hypothesis_id ? `hypothesis ${interview.hypothesis_id}` : "hypothesis not exposed"}
                </span>
              </div>
              {interview.question && <p className="mt-1 text-sm">Q: {interview.question}</p>}
              <p className="mt-1 font-mono text-[11px] text-ink-muted">
                {interview.thread_name ?? "thread not available"}
                {interview.deadline_at ? ` · deadline ${formatWhen(interview.deadline_at)}` : ""}
              </p>
              {interview.replies.length > 0 && (
                <ul className="mt-2 space-y-2">
                  {interview.replies.map((reply) => (
                    <li key={reply.message_name} className="bg-paper px-3 py-2 text-[13px]">
                      <p className="font-mono text-[11px] uppercase text-ink-muted">
                        {reply.classification} · {reply.sender_allowed ? "allowed sender" : "sender rejected"}
                      </p>
                      <p className="mt-1 whitespace-pre-wrap">{reply.raw_text}</p>
                    </li>
                  ))}
                </ul>
              )}
              {interview.outcome_summary && (
                <p className="mt-2 text-[12px] text-ink-muted">{interview.outcome_summary}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function CfiEvidence({ cfi }: { cfi: CfiDeskState }) {
  return (
    <Card title="Captured evidence" aside={<span className="text-[11px] text-ink-muted">tool provenance</span>}>
      {cfi.evidence.length === 0 ? (
        <p className="text-sm text-ink-muted">No evidence captured.</p>
      ) : (
        <ul className="max-h-[28rem] space-y-3 overflow-auto pr-1">
          {cfi.evidence.map((item) => (
            <li key={item.evidence_id} className="border-b border-line pb-2 last:border-0 last:pb-0">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="font-mono text-[11px] uppercase text-ink-muted">{item.evidence_id}</span>
                <span className="font-mono text-[11px] text-teal-deep">{item.source_tool}</span>
                <span className="font-mono text-[11px] text-ink-muted">round {item.round_number}</span>
              </div>
              <p className="mt-1 text-[13px]">{item.fact}</p>
              <p className="mt-1 break-all font-mono text-[11px] text-ink-muted">{item.source_uri}</p>
              <p className="font-mono text-[11px] text-ink-muted">captured {formatWhen(item.captured_at)}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function CfiArtifacts({ cfi }: { cfi: CfiDeskState }) {
  return (
    <Card title="Closure artifacts" aside={<span className="text-[11px] text-ink-muted">post-closure</span>}>
      <div className="space-y-4 text-sm">
        <ArtifactBlock title="Calendar site review">
          {cfi.site_review ? (
            <>
              <ExternalRef value={cfi.site_review.event_url} />
              <p className="mt-1 font-mono text-[11px] text-ink-muted">
                {formatWhen(cfi.site_review.start_at)} — {formatWhen(cfi.site_review.end_at)}
              </p>
              <p className="font-mono text-[11px] text-ink-muted">
                attendees {cfi.site_review.attendees.length}
              </p>
            </>
          ) : cfi.site_review_error ? (
            <ErrorText>{cfi.site_review_error}</ErrorText>
          ) : (
            <p className="text-ink-muted">Not filed yet.</p>
          )}
        </ArtifactBlock>
        <ArtifactBlock title="Filed CFI report">
          {cfi.report ? (
            <>
              <ExternalRef value={cfi.report.document_url} />
              <p className="mt-1 font-mono text-[11px] text-ink-muted">
                {cfi.report.was_update ? "idempotent update" : "created"} · {cfi.report.document_id}
              </p>
            </>
          ) : cfi.report_error ? (
            <ErrorText>{cfi.report_error}</ErrorText>
          ) : (
            <p className="text-ink-muted">Not filed yet.</p>
          )}
        </ArtifactBlock>
      </div>
    </Card>
  );
}

function CfiActivity({ cfi }: { cfi: CfiDeskState }) {
  return (
    <Card title="CFI activity" aside={<span className="text-[11px] text-ink-muted">deterministic event projection</span>}>
      {cfi.activity.length === 0 ? (
        <p className="text-sm text-ink-muted">No activity events recorded.</p>
      ) : (
        <ol className="space-y-2">
          {cfi.activity.map((item) => (
            <li key={item.activity_id} className="grid gap-1 border-b border-line pb-2 last:border-0 last:pb-0 sm:grid-cols-[10rem_1fr]">
              <div>
                <p className="font-mono text-[11px] uppercase text-teal-deep">{item.kind}</p>
                <p className="font-mono text-[11px] text-ink-muted">{item.node}</p>
              </div>
              <div>
                <p className="text-[13px]">{item.detail}</p>
                {item.at && <p className="font-mono text-[11px] text-ink-muted">{formatWhen(item.at)}</p>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function ArtifactBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wide text-ink-muted">{title}</p>
      <div className="mt-1">{children}</div>
    </div>
  );
}

function CfiStatus({
  value,
  launchStatus,
}: {
  value: CfiOutcome | null;
  launchStatus: string;
}) {
  const text = value ?? (launchStatus === "failed" ? "handoff_failed" : "in_progress");
  const tone = value === "root_cause_identified" ? "text-teal-deep" : "text-warn";
  return (
    <span className={`font-mono text-[11px] uppercase ${tone}`}>
      {text.replaceAll("_", " ")}
    </span>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-line bg-paper px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-ink-muted">{label}</p>
      <p className="mt-1 font-mono text-lg tabular">{value}</p>
    </div>
  );
}

function Field({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-ink-muted">{k}</dt>
      <dd className={mono ? "break-all font-mono text-[12px]" : "text-[13px]"}>{v}</dd>
    </div>
  );
}

function ErrorText({ children }: { children: React.ReactNode }) {
  return <p className="mt-2 break-words text-[13px] text-critical">{children}</p>;
}

function ExternalRef({ value }: { value: string }) {
  if (/^https?:\/\//i.test(value)) {
    return (
      <a
        href={value}
        target="_blank"
        rel="noreferrer"
        className="break-all text-teal-deep underline-offset-2 hover:underline"
      >
        {value}
      </a>
    );
  }
  return <span className="break-all font-mono text-[12px] text-ink-muted">{value}</span>;
}

function formatWhen(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toISOString().replace("T", " ").replace(".000Z", " UTC");
}
