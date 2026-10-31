"use client";
import { useState } from "react";
import Link from "next/link";
import PageHeader from "@/components/PageHeader";
import { Card, Empty, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { RiskStatusChip, type RiskHousehold, type RiskMandate } from "@/app/workspace/components/artifacts/RiskProfileCard";

const OPEN = ["more_risk", "less_risk", "none_on_file"];

export default function RiskProfiles() {
  const { data, loading, refetch } = useApi<any>("/api/risk-profiles");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deciding, setDeciding] = useState<{ id: string; decision: "adopt" | "keep"; reason: string } | null>(null);

  if (loading || !data) return <Spinner />;
  const s = data.summary;
  const canDecide: boolean = data.can_decide;
  const homes: RiskHousehold[] = data.households;
  const rows = homes.flatMap((h) => (h.mandates.length ? h.mandates.map((m) => ({ h, m })) : [{ h, m: null as RiskMandate | null }]));

  async function decide() {
    if (!deciding) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<any>(`/api/risk-profiles/mandates/${deciding.id}/decide`, {
        method: "POST", body: { decision: deciding.decision, reason: deciding.reason || null },
      });
      setMsg(`${r.mandate}: ${r.risk_profile} · ${r.capacity_for_loss}`);
      setDeciding(null);
      await refetch();
    } catch (e: any) {
      setMsg(e?.message ?? "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  const ready = deciding && (deciding.decision === "adopt" || deciding.reason.trim().length >= 3);

  return (
    <div>
      <PageHeader title="Risk profiles" sub="What each client's questionnaire indicates, beside what is on file." />
      {msg && <div className="mb-4 font-mono text-xs text-ink-muted" role="status" data-testid="risk-profile-status">{msg}</div>}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="risk-profile-summary">
        {[
          ["More risk than answers", s.more_risk, s.more_risk ? "text-crit" : "text-ink-muted"],
          ["Less risk than answers", s.less_risk, s.less_risk ? "text-warn" : "text-ink-muted"],
          ["Aligned", s.aligned + s.kept, "text-ok"],
          ["Not taken", s.not_taken + s.not_scored, s.not_taken + s.not_scored ? "text-warn" : "text-ink-muted"],
        ].map(([label, v, tone]: any) => (
          <Card key={label}>
            <div className="tile-label">{label}</div>
            <div className={`font-mono text-2xl tabular-nums ${tone}`}>{v}</div>
          </Card>
        ))}
      </div>

      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5" data-testid="risk-profile-basis">
          <span className="chip text-ink-muted">indicated: lower of tolerance and capacity</span>
          <span className="chip text-ink-muted">equity ceiling: low 40% · medium 70% · high 100%</span>
          {s.tension > 0 && <span className="chip text-warn">{s.tension} with tolerance and capacity apart</span>}
        </div>
      </Card>

      {rows.length === 0 ? <Empty>No households.</Empty> : (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="risk-profile-rows">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2 font-medium">Mandate</th>
                  <th className="px-4 py-2 font-medium">Tolerance</th>
                  <th className="px-4 py-2 font-medium">Capacity</th>
                  <th className="px-4 py-2 font-medium">Indicated</th>
                  <th className="px-4 py-2 font-medium">On file</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 text-right font-medium">Decide</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-soft">
                {rows.map(({ h, m }) => {
                  const a = h.assessment;
                  const status = m?.status ?? h.status;
                  const taken = a?.status === "scored";
                  const mine = !!m && deciding?.id === m.mandate_id;
                  return (
                    <tr key={m?.mandate_id ?? h.household_id}>
                      <td className="px-4 py-2">
                        <div className="text-ink">{m?.mandate ?? h.household}</div>
                        {m && <div className="text-xs text-ink-muted">{h.household}</div>}
                      </td>
                      <td className="px-4 py-2 font-mono text-xs text-ink-muted">{a?.tolerance ?? "—"}</td>
                      <td className="px-4 py-2 font-mono text-xs text-ink-muted">{a?.capacity ?? "—"}</td>
                      <td className="px-4 py-2 font-mono text-xs text-ink" title={a?.drivers.join(" · ").replace(/_/g, " ") || undefined}>{a?.indicated ?? "—"}</td>
                      <td className="px-4 py-2 font-mono text-xs text-ink-muted">{m ? `${m.risk_profile ?? "—"} · ${m.capacity_for_loss ?? "—"}` : "—"}</td>
                      <td className="px-4 py-2" title={m?.decision?.reason || undefined}><RiskStatusChip status={status} /></td>
                      <td className="px-4 py-2 text-right">
                        {!taken && <Link className="btn-outline text-xs" href={`/canvas/questionnaire?household_id=${h.household_id}`}>Questionnaire</Link>}
                        {taken && m && OPEN.includes(status) && !mine && (
                          <div className="flex justify-end gap-1.5">
                            <button className="btn-outline text-xs" data-testid="risk-adopt" disabled={!canDecide || busy}
                              title={!canDecide ? "Only an adviser decides" : undefined}
                              onClick={() => setDeciding({ id: m.mandate_id, decision: "adopt", reason: "" })}>Adopt</button>
                            <button className="btn-outline text-xs" data-testid="risk-keep" disabled={!canDecide || busy}
                              title={!canDecide ? "Only an adviser decides" : undefined}
                              onClick={() => setDeciding({ id: m.mandate_id, decision: "keep", reason: "" })}>Keep</button>
                          </div>
                        )}
                        {mine && deciding && (
                          <div className="flex flex-wrap items-center justify-end gap-1.5" data-testid="risk-confirm">
                            {deciding.decision === "adopt"
                              ? <span className="chip text-ink">{a?.indicated} · {a?.capacity}</span>
                              : <input className="input text-xs" aria-label="Reason" placeholder="Reason" value={deciding.reason}
                                  onChange={(e) => setDeciding({ ...deciding, reason: e.target.value })} />}
                            <button className="btn-primary text-xs" data-testid="risk-confirm-yes" disabled={busy || !ready} onClick={decide}>Confirm</button>
                            <button className="btn-outline text-xs" disabled={busy} onClick={() => setDeciding(null)}>Not now</button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
