"use client";
import { useState } from "react";
import { Scale, ShieldCheck } from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { Card, Spinner } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { api } from "@/lib/api";
import { titleCase } from "@/lib/format";

const CAT_STYLE: Record<string, string> = {
  suitability: "bg-navy-100 text-navy-700", conduct: "bg-caution/10 text-caution",
  disclosure: "bg-navy-50 text-ink-muted", best_interest: "bg-gold-soft/40 text-gold-dark",
  aml: "bg-critical/10 text-critical", tax: "bg-positive/10 text-positive", records: "bg-navy-50 text-ink-muted",
};

export default function Regulatory() {
  const { data, loading, refetch } = useApi<any>("/api/admin/compliance");
  const [busy, setBusy] = useState<string | null>(null);
  if (loading || !data) return <Spinner />;

  async function setRule(rule_id: string, patch: any) {
    setBusy(rule_id);
    try { await api("/api/admin/compliance/rule", { method: "PUT", body: { rule_id, ...patch } }); refetch(); }
    finally { setBusy(null); }
  }

  return (
    <div>
      <PageHeader title="Regulatory framework"
        sub="The machine-readable obligations every agent reasons against — and the ledger proves compliance to." />

      <Card className="mb-5">
        <div className="flex items-center gap-2.5">
          <span className="h-10 w-10 rounded-xl bg-navy-800 text-white flex items-center justify-center"><Scale size={20} /></span>
          <div>
            <div className="font-semibold text-ink">{data.name}</div>
            <div className="text-xs text-ink-muted">{data.authority} · regime <b>{data.regime}</b> · framework <b>v{data.version}</b> · {data.rules.length} rules</div>
          </div>
          <span className="chip bg-navy-50 text-ink-muted ml-auto">resolved from firm jurisdiction</span>
        </div>
      </Card>

      <Card>
        <div className="font-semibold text-ink mb-1 flex items-center gap-2"><ShieldCheck size={17} /> Obligations</div>
        <p className="text-xs text-ink-muted mb-3">Toggle a rule off, or change its severity — applied to the next assessment, and recorded against this framework version.</p>
        <div className="divide-y divide-navy-50">
          {data.rules.map((r: any) => (
            <div key={r.id} className={`py-3 flex items-start gap-3 ${r.enabled ? "" : "opacity-50"}`}>
              <button onClick={() => setRule(r.id, { enabled: !r.enabled })} disabled={busy === r.id}
                className={`mt-0.5 h-5 w-9 rounded-full transition-colors relative shrink-0 ${r.enabled ? "bg-positive" : "bg-navy-200"}`}>
                <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${r.enabled ? "left-4.5" : "left-0.5"}`} style={{ left: r.enabled ? "1.125rem" : "0.125rem" }} />
              </button>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium text-ink text-sm">{r.title}</span>
                  <span className="chip bg-navy-800 text-white">{r.code}</span>
                  <span className={`chip ${CAT_STYLE[r.category] || "bg-navy-50 text-ink-muted"}`}>{titleCase(r.category)}</span>
                </div>
                <div className="text-xs text-ink-muted mt-0.5">{r.citation}</div>
                {r.description && <div className="text-xs text-ink-soft mt-0.5">{r.description}</div>}
                <div className="text-[11px] text-ink-muted mt-0.5">Applies to: {r.applies_to.join(", ")}</div>
              </div>
              <select className="input w-28 shrink-0" value={r.severity} disabled={busy === r.id}
                onChange={(e) => setRule(r.id, { severity: e.target.value })}>
                <option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
              </select>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
