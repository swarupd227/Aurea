"use client";

import { Card } from "@/components/ui";
import { reviewDay } from "@/app/workspace/components/artifacts/ReviewsCard";
import { useViewNav } from "./ViewNav";

const th = "px-4 py-2 font-medium";
const head = "text-left text-[10px] uppercase tracking-wide text-ink-muted";
const title = "border-b border-border-soft px-4 py-2 text-sm font-medium text-ink";
const PEP_TONE: Record<string, string> = { outstanding: "text-warn", declined: "text-crit", approved: "text-ok" };
const STR: Record<string, [string, string]> = {
  filed_in_time: ["filed in time", "text-ok"], filed_late: ["filed late", "text-crit"], overdue: ["overdue", "text-crit"],
  open: ["open", "text-warn"], not_measured: ["not measured", "text-ink-muted"], no_report: ["no report", "text-ink-muted"],
};
const REPORT: Record<string, string> = { filed: "text-ok", filed_late: "text-warn", due: "text-warn", overdue: "text-crit" };

function figureLabel(f: any): string {
  if (f.key === "in_pml_cash_threshold") return `cash line INR ${Number(f.value).toLocaleString("en-IN")}`;
  if (f.key === "in_pml_str_working_days") return `STR ${f.value} working days`;
  return `monthly reports by day ${f.value}`;
}

/** Politically exposed persons and what each relationship still needs (B-390). An action is a sentence to Astra. */
export function PepSection({ rows }: { rows: any[] }) {
  const nav = useViewNav();
  if (!rows.length) return null;
  return (
    <Card className="mb-4 overflow-hidden p-0">
      <div className={title}>Politically exposed persons</div>
      <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="aml-pep">
        <thead><tr className={head}><th className={th}>Relationship</th><th className={th}>Known by</th><th className={th}>Risk</th><th className={th}>Senior approval</th><th className={`${th} text-right`}>Record</th></tr></thead>
        <tbody className="divide-y divide-border-soft">
          {rows.map((p) => (
            <tr key={p.case_id ?? p.holder_id} title={p.approval?.reason}>
              <td className="px-4 py-2"><div className="text-ink">{p.case}</div><div className="text-xs text-ink-muted">{p.case_id ? (p.account_open ? "account open" : "not yet open") : "no onboarding case"}</div></td>
              <td className="px-4 py-2"><div className="flex flex-wrap gap-1">{p.people.flatMap((x: any) => x.how.map((h: string) => <span key={x.name + h} className="chip text-ink-muted">{x.name} · {h}</span>))}</div></td>
              <td className="px-4 py-2"><span className={`chip ${p.risk_tier === "high" ? "text-crit" : "text-ink-muted"}`}>{p.risk_tier ?? "not rated"}</span></td>
              <td className="px-4 py-2"><div className="flex flex-wrap gap-1">
                <span className={`chip ${PEP_TONE[p.status]}`}>{p.status}</span>
                {p.approval && <span className="chip text-ink-muted">{p.approval.approver} · {reviewDay(p.approval.decided_on)}</span>}
                {p.needs.map((n: string) => <span key={n} className="chip text-warn">needs {n}</span>)}
              </div></td>
              <td className="px-4 py-2 text-right">
                {p.status !== "approved" && nav.say && (
                  <button className="btn-outline text-xs" data-testid="aml-pep-record"
                          onClick={() => nav.say?.(`Record the senior approval for the politically exposed relationship ${p.case} (${p.case_id ? `case ${p.case_id}` : `client ${p.holder_id}`}). Ask me for the decision, the approver, the reason and where it is filed.`)}>
                    Ask Astra
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </Card>
  );
}

/** The firm's screening lists as recorded, and who has not been screened since one changed. */
export function ScreeningListsSection({ data }: { data: any }) {
  const nav = useViewNav();
  const b = data.backlog;
  return (
    <Card className="mb-4 overflow-hidden p-0">
      <div className={`${title} flex flex-wrap items-center gap-1.5`}>
        Screening lists
        <span className="chip text-ink-muted">no list feed connected</span>
        {data.lists.some((l: any) => l.demonstration) && <span className="chip text-warn" data-testid="aml-lists-invented">invented demonstration entries</span>}
        <span className={`chip ${b.count ? "text-crit" : "text-ok"}`} data-testid="aml-backlog">{b.count ? `${b.count} to rescreen · oldest ${b.oldest_age_days}d` : "no rescreen backlog"}</span>
        {b.clients_without_party > 0 && <span className="chip text-ink-muted">{b.clients_without_party} clients with no party record, not screened here</span>}
        {b.count > 0 && nav.say && <button className="btn-primary ml-auto text-xs" data-testid="aml-rescreen" onClick={() => nav.say?.("Run the delta rescreen for the parties waiting since the screening list changed.")}>Ask Astra to rescreen</button>}
      </div>
      <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="aml-lists">
        <thead><tr className={head}><th className={th}>List</th><th className={th}>Version</th><th className={th}>Changed</th><th className={`${th} text-right`}>Names</th></tr></thead>
        <tbody className="divide-y divide-border-soft">
          {data.lists.map((l: any) => (
            <tr key={l.list_key}>
              <td className="px-4 py-2 text-ink">{l.label}</td>
              <td className="px-4 py-2 font-mono text-xs text-ink-muted">{l.version ?? "none recorded"}</td>
              <td className="px-4 py-2 font-mono text-xs text-ink-muted">{l.changed_on ? reviewDay(l.changed_on) : "—"}</td>
              <td className="px-4 py-2 text-right font-mono text-xs text-ink">{l.recorded ? l.entries : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
      {b.count > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-4 py-2">
          {b.items.map((i: any) => <span key={i.party_id} className="chip text-warn">{i.party} · {i.version} · {i.age_days}d</span>)}
        </div>
      )}
    </Card>
  );
}

/** Reporting to FIU-IND: officers, suspicious report timeliness and the monthly reports (B-390). */
export function FiuSection({ fiu }: { fiu: any }) {
  const nav = useViewNav();
  const t = fiu.str;
  return (
    <>
      <Card className="mb-4">
        <div className="flex flex-wrap gap-1.5" data-testid="fiu-officers">
          <span className={`chip ${fiu.officers.principal_officer ? "text-ink" : "text-crit"}`}>Principal Officer: {fiu.officers.principal_officer ?? "not recorded"}</span>
          <span className={`chip ${fiu.officers.designated_director ? "text-ink" : "text-warn"}`}>Designated Director: {fiu.officers.designated_director ?? "not recorded"}</span>
          {fiu.figures.map((f: any) => <span key={f.key} className="chip text-ink-muted" title={f.source}>{figureLabel(f)} · assumed{f.counsel ? ` · ${f.counsel.label}` : ""}</span>)}
        </div>
      </Card>
      <Card className="mb-4 overflow-hidden p-0">
        <div className={`${title} flex flex-wrap items-center gap-1.5`}>
          Suspicious report timeliness
          <span className={`chip ${t.late || t.overdue ? "text-crit" : "text-ok"}`} data-testid="fiu-timeliness">{t.in_time_pct == null ? "nothing measured" : `${t.in_time_pct}% in time · ${t.in_time} of ${t.measured}`}</span>
          {t.open > 0 && <span className="chip text-warn">{t.open} open</span>}
        </div>
        {t.items.length > 0 && (
          <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="fiu-str">
            <thead><tr className={head}><th className={th}>Household</th><th className={th}>State</th><th className={th}>Suspicion</th><th className={th}>Filed</th><th className={`${th} text-right`}>Working days</th><th className={th}>Principal Officer</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {t.items.map((r: any) => {
                const [label, tone] = STR[r.state] ?? [r.state, "text-ink-muted"];
                return (
                  <tr key={r.investigation_id}>
                    <td className="px-4 py-2 text-ink">{r.household}</td>
                    <td className="px-4 py-2"><span className={`chip ${tone}`}>{label}</span></td>
                    <td className="px-4 py-2 font-mono text-xs text-ink-muted">{r.suspicion_on ? reviewDay(r.suspicion_on) : "—"}</td>
                    <td className="px-4 py-2 font-mono text-xs text-ink-muted">{r.reported_on ? `${reviewDay(r.reported_on)} · ${r.reference}` : "—"}</td>
                    <td className="px-4 py-2 text-right font-mono text-xs text-ink">{r.working_days ?? "—"} / {t.line_working_days}</td>
                    <td className="px-4 py-2 text-xs text-ink-muted">{r.principal_officer ?? "not recorded"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table></div>
        )}
      </Card>
      <Card className="mb-4 overflow-hidden p-0">
        <div className={title}>Monthly reports to FIU-IND</div>
        <div className="overflow-x-auto"><table className="w-full text-sm" data-testid="fiu-reports">
          <thead><tr className={head}><th className={th}>Month</th><th className={th}>Report</th><th className={th}>Due</th><th className={th}>State</th><th className={th}>Reference</th><th className={`${th} text-right`}>Record</th></tr></thead>
          <tbody className="divide-y divide-border-soft">
            {fiu.reports.map((r: any) => (
              <tr key={r.kind + r.period}>
                <td className="px-4 py-2 font-mono text-xs text-ink">{r.period}</td>
                <td className="px-4 py-2 text-xs text-ink">{r.label}{r.transactions != null && <span className="chip ml-1 text-ink-muted">{r.transactions === 0 ? "nil" : `${r.transactions} transactions`}</span>}</td>
                <td className="px-4 py-2 font-mono text-xs text-ink-muted">{reviewDay(r.due_on)}</td>
                <td className="px-4 py-2"><span className={`chip ${REPORT[r.state] ?? ""}`}>{r.state.replace(/_/g, " ")}</span></td>
                <td className="px-4 py-2 font-mono text-xs text-ink-muted">{r.reference ?? "—"}</td>
                <td className="px-4 py-2 text-right">
                  {!r.reference && nav.say && <button className="btn-outline text-xs" data-testid="fiu-record" onClick={() => nav.say?.(`Record the ${r.label} for ${r.period} as filed with FIU-IND. Ask me for the filing reference.`)}>Ask Astra</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </Card>
    </>
  );
}
