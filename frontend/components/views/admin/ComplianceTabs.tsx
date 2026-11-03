"use client";

import { useState } from "react";
import { RefreshCw, AlertCircle, CheckCircle2, Search } from "lucide-react";
import { Card, Spinner, Empty, ErrorState } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { chip } from "../ViewHeader";
import { TableCard, TD, TH } from "./shared";

// ── Rule impact (a read) ──────────────────────────────────────────────────────

export function RuleImpactTab() {
  const { data: complianceData, loading: compLoading } = useApi<any>("/api/admin/compliance");
  const [ruleCode, setRuleCode] = useState("");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  async function check() {
    if (!ruleCode.trim()) return;
    setBusy(true);
    setResult(null);
    try {
      const data = await api<any>(`/api/admin/compliance/impact?rule_code=${encodeURIComponent(ruleCode.trim())}`);
      setResult(data);
    } catch (e: any) {
      setResult({ error: e.message });
    }
    setBusy(false);
  }

  return (
    <div className="space-y-4" data-testid="admin-rule-impact">
      <Card>
        <div className="mb-4 flex items-center gap-2 font-semibold text-ink"><Search size={16} /> Rule impact</div>
        <div className="mb-4 flex flex-wrap gap-3">
          <input
            className="input min-w-[12rem] flex-1"
            placeholder="Rule code, e.g. NZ-FMA-001"
            aria-label="Rule code"
            value={ruleCode}
            onChange={(e) => setRuleCode(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && check()}
            data-testid="admin-rule-code"
          />
          <button className="btn-primary" onClick={check} disabled={busy || !ruleCode.trim()} data-testid="admin-rule-check">
            {busy ? "Checking…" : "Check impact"}
          </button>
        </div>

        {!compLoading && complianceData?.rules && (
          <div className="flex flex-wrap gap-1.5">
            {complianceData.rules.slice(0, 8).map((r: any) => (
              <button key={r.code} onClick={() => setRuleCode(r.code)} aria-pressed={ruleCode === r.code}
                className={`chip font-mono ${ruleCode === r.code ? "bg-border text-ink" : "bg-border-soft text-ink-soft hover:text-ink"}`}>
                {r.code}
              </button>
            ))}
          </div>
        )}
      </Card>

      {result && !result.error && (
        <Card className="p-0" data-testid="admin-rule-result">
          <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-4 py-3">
            {result.affected_count > 0
              ? <AlertCircle size={16} className="text-warn" />
              : <CheckCircle2 size={16} className="text-ok" />}
            <span className={`${chip} font-mono`}>{result.rule_code}</span>
            <span className={`chip tabular-nums ${result.affected_count > 0 ? "bg-warn-bg text-warn" : "bg-ok-bg text-ok"}`}>
              {result.affected_count} affected
            </span>
          </div>
          {result.affected_recommendations?.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border-soft">
                    <th className={TH}>Title</th>
                    <th className={TH}>Agent</th>
                    <th className={TH}>Status</th>
                    <th className={TH}>Subject</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-soft">
                  {result.affected_recommendations.map((r: any) => (
                    <tr key={r.id}>
                      <td className={`${TD} text-ink`}>{r.title}</td>
                      <td className={`${TD} text-ink-muted`}>{r.agent_key?.replace(/_/g, " ")}</td>
                      <td className={TD}><span className="chip bg-warn-bg text-warn">{r.status}</span></td>
                      <td className={`${TD} text-ink-muted`}>{r.subject_label || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
      {result?.error && (
        <Card><ErrorState what="the rule impact" message={result.error} onRetry={check} /></Card>
      )}
    </div>
  );
}

// ── Audit trail (a read) ──────────────────────────────────────────────────────

const AUDIT_EVENT_LABELS: Record<string, string> = {
  "auth.login": "Sign in", "auth.invite_accepted": "Account activated",
  "auth.reset_accepted": "Password reset", "auth.password_changed": "Password changed",
  "user.created": "User created", "user.activated": "User activated",
  "user.deactivated": "User deactivated", "user.invite_resent": "Invite resent",
  "user.password_reset_issued": "Password reset issued",
  "client.created": "Client added", "client.adviser_assigned": "Adviser assigned",
  "recommendation.decision": "Recommendation decided", "recommendation.recommendation": "Recommendation created",
  "autonomy.tier_changed": "Autonomy tier changed",
};

export function AuditTab() {
  const [filter, setFilter] = useState("all");
  const { data: events, loading, error, refetch } = useApi<any[]>("/api/admin/audit?limit=150");

  const categories = ["all", "access", "recommendation", "autonomy", "client"];
  const filtered = filter === "all" ? events : events?.filter((e) => e.category === filter);

  return (
    <div className="max-w-5xl space-y-3" data-testid="admin-audit">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex w-fit max-w-full flex-wrap overflow-hidden rounded border border-border text-sm">
          {categories.map((c) => (
            <button key={c} onClick={() => setFilter(c)} aria-pressed={filter === c} data-testid={`admin-audit-filter-${c}`}
              className={`px-3 py-1.5 ${filter === c ? "bg-border-soft text-ink" : "text-ink-muted hover:text-ink"}`}>
              {titleCase(c)}
            </button>
          ))}
        </div>
        <button className="btn-outline ml-auto px-2 py-1 text-xs" onClick={() => refetch()}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {loading && !events ? <Card><Spinner /></Card> : error && !events ? (
        <Card><ErrorState what="the audit trail" message={error} onRetry={refetch} /></Card>
      ) : !filtered?.length ? (
        <Card><Empty>No audit events</Empty></Card>
      ) : (
        <TableCard testid="admin-audit-table">
          <thead>
            <tr className="border-b border-border-soft">
              <th className={TH}>Time</th>
              <th className={TH}>Event</th>
              <th className={TH}>Actor</th>
              <th className={TH}>Subject</th>
              <th className={TH}>Detail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {filtered.map((e, i) => (
              <tr key={i} className="text-xs">
                <td className={`${TD} whitespace-nowrap font-mono text-ink-muted`}>{new Date(e.ts).toLocaleString()}</td>
                <td className={TD}>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={`${chip} text-[10px]`}>{titleCase(e.category)}</span>
                    <span className="text-ink">{AUDIT_EVENT_LABELS[e.event_type] || titleCase(e.event_type)}</span>
                  </div>
                </td>
                <td className={`${TD} text-ink-soft`}>{e.actor || "—"}</td>
                <td className={`${TD} text-ink-soft`}>{e.subject || "—"}</td>
                <td className={`${TD} font-mono text-[10px] text-ink-muted`}>
                  {e.detail ? Object.entries(e.detail).map(([k, v]) => `${k}:${v}`).join(" · ") : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </TableCard>
      )}
    </div>
  );
}
