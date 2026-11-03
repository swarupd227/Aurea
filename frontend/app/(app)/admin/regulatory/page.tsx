"use client";
import { useState } from "react";
import { Scale, ShieldCheck, ClipboardList, AlertTriangle, Check } from "lucide-react";
import { Card, Spinner, StatTile } from "@/components/ui";
import { RulebookView } from "@/components/views/RulebookView";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";

function ConflictsAndWsp() {
  const { data, loading, refetch } = useApi<any>("/api/compliance-program/summary");
  const { data: conflicts, refetch: refetchConflicts } = useApi<any>("/api/compliance-program/conflicts");
  const { data: wsp, refetch: refetchWsp } = useApi<any>("/api/compliance-program/wsp");
  const [busy, setBusy] = useState<string | null>(null);

  if (loading || !data) return <Spinner />;

  const overdueKeys = new Set(
    (data.conflicts.overdue_review as any[]).map((r) => r.conflict_key)
  );
  const staleKeys = new Set(
    (data.evidence_coverage.stale_evidence as any[]).map((r) => r.rule_key)
  );

  async function markReviewed(id: string) {
    setBusy(id);
    try {
      await api(`/api/compliance-program/conflicts/${id}`, { method: "PATCH", body: { mark_reviewed: true } });
      await Promise.all([refetch(), refetchConflicts()]);
    } finally {
      setBusy(null);
    }
  }

  async function logEvidence(id: string) {
    setBusy(id);
    try {
      await api(`/api/compliance-program/wsp/${id}`, { method: "PATCH", body: { log_evidence: true } });
      await Promise.all([refetch(), refetchWsp()]);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-5 space-y-5">
      <div className="flex items-center gap-2">
        <ClipboardList size={18} className="text-ink-muted" />
        <h2 className="text-lg font-semibold text-ink">Conflicts inventory & WSP grid</h2>
        <span className="text-xs text-ink-muted">Evidence for the framework above</span>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <StatTile label="Active conflicts" value={data.conflicts.active} />
        <StatTile label="Overdue for review" value={overdueKeys.size} accent={overdueKeys.size ? "warn" : "ok"} />
        <StatTile label="WSP evidence coverage" value={`${data.evidence_coverage.coverage_pct}%`}
          hint={`${data.evidence_coverage.manual_attestation} manual attestation-only row(s)`}
          accent={data.evidence_coverage.coverage_pct >= 75 ? "ok" : "warn"} />
      </div>

      <Card>
        <div className="font-semibold text-ink mb-1 flex items-center gap-2"><ShieldCheck size={17} /> Conflicts inventory</div>
        <p className="text-xs text-ink-muted mb-3">Every conflict the firm has identified, its mitigation, and who owns keeping the answer current.</p>
        <div className="divide-y divide-border-soft">
          {conflicts?.items?.map((c: any) => (
            <div key={c.id} className="py-3 flex items-start gap-3">
              {overdueKeys.has(c.conflict_key)
                ? <AlertTriangle size={16} className="mt-0.5 text-warn shrink-0" />
                : <Check size={16} className="mt-0.5 text-ok shrink-0" />}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium text-ink text-sm">{c.title}</span>
                  <span className="chip bg-border-soft text-ink-muted">{titleCase(c.status)}</span>
                  {c.owner && <span className="text-xs text-ink-muted">owner: {c.owner}</span>}
                </div>
                <div className="text-xs text-ink-muted mt-0.5">{c.mitigation}</div>
                <div className="text-[11px] text-ink-faint mt-0.5">
                  {c.last_reviewed_at ? `Last reviewed ${c.last_reviewed_at.slice(0, 10)}` : "Never reviewed"}
                </div>
              </div>
              <button className="btn-outline text-xs shrink-0" disabled={busy === c.id}
                onClick={() => markReviewed(c.id)}>
                {busy === c.id ? "Saving…" : "Mark reviewed"}
              </button>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <div className="font-semibold text-ink mb-1 flex items-center gap-2"><Scale size={17} /> WSP grid</div>
        <p className="text-xs text-ink-muted mb-3">Rule → designated owner → supervisory activity → frequency → evidence. Attestation-only rows are the risk backlog.</p>
        <div className="divide-y divide-border-soft">
          {wsp?.items?.map((r: any) => (
            <div key={r.id} className="py-3 flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium text-ink text-sm">{r.obligation}</span>
                  <span className={`chip ${r.evidence_automated ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"}`}>
                    {r.evidence_automated ? "Automated evidence" : "Manual attestation"}
                  </span>
                  {staleKeys.has(r.rule_key) && <span className="chip bg-crit-bg text-crit">Stale</span>}
                </div>
                <div className="text-xs text-ink-muted mt-0.5">{r.designated_role} · {r.frequency}</div>
                <div className="text-[11px] text-ink-faint mt-0.5">
                  {r.last_evidence_at ? `Evidence logged ${r.last_evidence_at.slice(0, 10)}` : "No evidence logged"}
                </div>
              </div>
              <button className="btn-outline text-xs shrink-0" disabled={busy === r.id}
                onClick={() => logEvidence(r.id)}>
                {busy === r.id ? "Saving…" : "Log evidence"}
              </button>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

export default function Regulatory() {
  return (
    <div>
      <RulebookView />
      <ConflictsAndWsp />
    </div>
  );
}
