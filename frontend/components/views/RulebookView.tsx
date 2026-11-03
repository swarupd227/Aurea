"use client";

import { useState } from "react";
import { Card, ErrorState, SkeletonList } from "@/components/ui";
import { api, getUser } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";

const SEV_TONE: Record<string, string> = { high: "text-crit", medium: "text-warn", low: "text-ink-muted" };

/** `/api/rulebook` and the read/set_compliance_rule tools (backend `aurea_core/rulebook.py`). */
export interface Rulebook {
  regime: string; name: string; version: string; authority: string; impact_days: number;
  rules: { id: string; code: string; citation: string; title: string; category: string; severity: string; default_severity: string;
           enabled: boolean; applies_to: string[]; fired_90d: number }[];
  settings: { key: string; label: string; kind: string; value: any; default: any; set_by_firm: boolean; owner: string }[];
}

const shown = (s: Rulebook["settings"][number]) =>
  s.kind === "bool" ? (s.value ? "On" : "Off") : s.kind === "share" ? `${Math.round(Number(s.value || 0) * 100)}%`
    : Array.isArray(s.value) ? s.value.join(", ") || "Nothing" : Number(s.value).toLocaleString();

/** The regime's rules as the firm runs them: checked or not, severity, how often each failed. Figures and chips only. */
export function RulesTable({ book, onChange, busy }: { book: Rulebook; onChange?: (id: string, patch: any) => void; busy?: string | null }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs" data-testid="rulebook-rules">
        <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1.5 font-medium">Rule</th><th className="px-3 py-1.5 font-medium">Applies to</th>
          <th className="px-3 py-1.5 text-right font-medium">Failed · {book.impact_days}d</th>
          <th className="px-3 py-1.5 font-medium">Severity</th><th className="px-3 py-1.5 font-medium">Checked</th>
        </tr></thead>
        <tbody className="divide-y divide-border-soft">
          {book.rules.map((r) => (
            <tr key={r.id} className={r.enabled ? "" : "opacity-60"}>
              <td className="px-3 py-1.5">
                <div className="flex flex-wrap items-center gap-1.5"><span className="chip font-mono">{r.code}</span><span className="text-ink">{r.title}</span></div>
                <div className="mt-0.5 text-[11px] text-ink-muted">{r.citation}</div>
              </td>
              <td className="px-3 py-1.5 text-ink-muted">{r.applies_to.join(", ").replace("*", "every agent")}</td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{r.fired_90d}</td>
              <td className="px-3 py-1.5">
                {onChange ? (
                  <select className="input w-24 py-1 text-xs" value={r.severity} disabled={busy === r.id} aria-label={`Severity of ${r.code}`}
                    onChange={(e) => onChange(r.id, { severity: e.target.value })}>
                    <option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
                  </select>
                ) : <span className={`chip ${SEV_TONE[r.severity] ?? ""}`}>{r.severity}</span>}
                {r.severity !== r.default_severity && <span className="chip ml-1 text-ink-muted">firm set</span>}
              </td>
              <td className="px-3 py-1.5">
                {onChange ? (
                  <button className={`chip ${r.enabled ? "text-ok" : "text-crit"}`} disabled={busy === r.id} data-testid="rule-toggle"
                    onClick={() => onChange(r.id, { enabled: !r.enabled })}>{r.enabled ? "Checked" : "Off"}</button>
                ) : <span className={`chip ${r.enabled ? "text-ok" : "text-crit"}`}>{r.enabled ? "Checked" : "Off"}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The firm's rulebook and agent controls. Compliance changes a rule: beside the conversation through Astra and its
 * Confirm card, on a page with a reason that goes on the audit log. */
export function RulebookView() {
  const nav = useViewNav();
  const { data, loading, error, refetch } = useApi<Rulebook>("/api/rulebook");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const isCompliance = typeof window !== "undefined" && getUser()?.role === "compliance";
  if (error) return <div className="view-container"><Card><ErrorState what="the rulebook" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data) return <div className="view-container"><SkeletonList count={4} /></div>;

  async function change(id: string, patch: any) {
    const rule = data!.rules.find((r) => r.id === id);
    if (nav.say) {
      const what = patch.enabled === undefined ? `set its severity to ${patch.severity}` : patch.enabled ? "turn it back on" : "turn it off";
      return nav.say(`For the compliance rule ${rule?.code} (${rule?.title}), ${what}.`);
    }
    setBusy(id);
    setFailed(null);
    try {
      await api("/api/admin/compliance/rule", { method: "PUT", body: { rule_id: id, ...patch, reason } });
      setReason("");
      refetch();
    } catch (e: any) {
      setFailed(e?.message || "Not changed.");
    } finally { setBusy(null); }
  }
  const canChange = isCompliance && (!!nav.say || !!reason.trim());
  const off = data.rules.filter((r) => !r.enabled).length;

  return (
    <div className="view-container" data-testid="rulebook-view">
      <ViewHeader title="Rulebook" meta={
        <>
          <span className={chip}>{data.name} v{data.version}</span>
          <span className={`${chip} tabular-nums`}>{data.rules.length} rules</span>
          {off > 0 && <span className="chip bg-crit-bg text-crit tabular-nums">{off} off</span>}
        </>
      } />
      {isCompliance && !nav.say && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <input className="input max-w-md" placeholder="Reason for a change (on the audit log)" aria-label="Reason for a rule change"
            value={reason} onChange={(e) => setReason(e.target.value)} data-testid="rule-reason" />
          {!reason.trim() && <span className="text-xs text-ink-muted">a reason first</span>}
        </div>
      )}
      {failed && <p className="mb-3 rounded-lg bg-crit-bg px-3 py-2 text-sm text-crit" role="alert">{failed}</p>}
      <Card className="mb-4 overflow-hidden p-0">
        <RulesTable book={data} onChange={canChange ? change : undefined} busy={busy} />
      </Card>
      <Card className="overflow-hidden p-0">
        <div className="border-b border-border-soft px-3 py-2 text-sm font-medium text-ink">Agent controls</div>
        <table className="w-full text-xs" data-testid="rulebook-controls"><tbody className="divide-y divide-border-soft">
          {data.settings.map((s) => (
            <tr key={s.key}>
              <td className="px-3 py-1.5 text-ink">{s.label}</td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink">{shown(s)}</td>
              <td className="px-3 py-1.5"><div className="flex flex-wrap gap-1">
                <span className="chip text-ink-muted">{s.owner}</span>{s.set_by_firm && <span className="chip">firm set</span>}
              </div></td>
            </tr>
          ))}
        </tbody></table>
      </Card>
    </div>
  );
}
