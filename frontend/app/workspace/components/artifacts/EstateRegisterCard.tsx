import { money } from "@/lib/format";
import { reviewDay } from "./ReviewsCard";

/** B-398: the asset-and-wishes register for a client's lawyer and the yearly will check. Figures, tables and chips. */

const CHECK: Record<string, [string, string]> = {
  no_will: ["no will on file", "text-warn"], never_checked: ["never checked", "text-warn"], due: ["check due", "text-warn"],
  changed_since: ["changed since check", "text-warn"], needs_change: ["will needs change", "text-crit"], checked: ["checked", "text-ok"],
};
const NOMINATION: Record<string, [string, string]> = {
  nominated: ["nominated", "text-ok"], opted_out: ["opted out", "text-ink-muted"], not_on_file: ["no nomination on file", "text-warn"],
  not_applicable: ["passes by will or deed", "text-ink-muted"],
};
const said = (s: string) => s.replace(/_/g, " ");

function Nomination({ n }: { n: any }) {
  const [text, tone] = NOMINATION[n.state] ?? [n.state, "text-ink-muted"];
  return <span className={`chip ${tone}`} title={n.from ?? undefined}>{n.state === "nominated" ? n.nominees.join(", ") : text}</span>;
}

export function RegisterAssets({ view, limit }: { view: any; limit?: number }) {
  const rows: any[] = limit ? view.assets.slice(0, limit) : view.assets;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs" data-testid="estate-register">
        <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
          <th className="px-3 py-1.5 font-medium">Asset</th><th className="px-3 py-1.5 font-medium">Whose</th>
          <th className="px-3 py-1.5 text-right font-medium">Figure</th><th className="px-3 py-1.5 font-medium">Nomination</th>
        </tr></thead>
        <tbody className="divide-y divide-border-soft">
          {rows.map((a) => (
            <tr key={a.key}>
              <td className="px-3 py-1.5 text-ink" title={a.source ?? undefined}>{a.label}{a.held_as ? <span className="text-ink-muted"> · {said(a.held_as)}</span> : null}</td>
              <td className="px-3 py-1.5 text-ink-muted">{a.owner}</td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-ink" title={a.value_on ? `as of ${reviewDay(a.value_on)}` : undefined}>
                {a.value === null ? "not on file" : money(a.value, a.currency)}{a.synthetic ? " *" : ""}
              </td>
              <td className="px-3 py-1.5"><Nomination n={a.nomination} /></td>
            </tr>
          ))}
          {!limit && view.liabilities.map((d: any) => (
            <tr key={d.key}>
              <td className="px-3 py-1.5 text-ink" title={d.source}>{d.label} <span className="chip text-ink-muted">debt</span></td>
              <td className="px-3 py-1.5 text-ink-muted">{d.owner}</td>
              <td className="px-3 py-1.5 text-right font-mono tabular-nums text-warn">{money(d.value, d.currency)}</td>
              <td />
            </tr>
          ))}
        </tbody>
      </table>
      {limit && view.assets.length > limit && <div className="px-3 py-1.5"><span className="chip text-ink-muted">+{view.assets.length - limit} more</span></div>}
      {view.assets.some((a: any) => a.synthetic) && <div className="px-3 py-1.5"><span className="chip text-warn">* synthetic price</span></div>}
    </div>
  );
}

export function WillChecks({ view }: { view: any }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="will-checks">
      {view.people.map((p: any) => {
        const [text, tone] = CHECK[p.check.state] ?? [p.check.state, "text-ink-muted"];
        return (
          <div key={p.person_id} className="space-y-1.5 px-3 py-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-auto text-sm text-ink">{p.name}</span>
              <span className={`chip ${tone}`} data-testid="will-check-state" title={p.check.findings ?? undefined}>{text}</span>
              {p.will && <span className="chip text-ink-muted" title={p.will.document}>signed {reviewDay(p.will.signed_on)}</span>}
              {p.check.last_checked_on && <span className="chip text-ink-muted">checked {reviewDay(p.check.last_checked_on)} · due {reviewDay(p.check.due_on)}</span>}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {p.witnesses?.length ? <span className="chip text-ink-muted" title={p.witnesses.join(", ")}>{p.witnesses.length} witness{p.witnesses.length === 1 ? "" : "es"}</span> : null}
              {p.executor && <span className="chip text-ink-muted">executor {p.executor}{p.executor_told ? " · told" : ""}</span>}
              {p.flags.map((f: string) => <span key={f} className="chip text-warn">{f}</span>)}
              {p.check.changes.map((c: string) => <span key={c} className="chip text-warn">{c}</span>)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function TrustTriggers({ view }: { view: any }) {
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="trust-triggers">
      {view.trust_triggers.length === 0 ? <span className="chip text-ink-muted">no trust trigger on file</span>
        : view.trust_triggers.map((t: any) => <span key={t.key} className="chip text-info" title={t.evidence.join("; ")}>{t.label}</span>)}
    </div>
  );
}

export function EstateRegisterCard({ result }: { result: any }) {
  if (!result?.applies) return null;
  if (!result.household_id) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="estate-register-card">
        <div className="border-b border-border-soft px-3 py-2 text-sm font-medium text-ink">Asset registers</div>
        <div className="divide-y divide-border-soft">
          {(result.households ?? []).map((h: any) => (
            <div key={h.household_id} className="flex flex-wrap items-center gap-1.5 px-3 py-2">
              <span className="mr-auto text-sm text-ink">{h.household}</span>
              <span className="chip tabular-nums text-ink-muted">{h.assets} assets</span>
              {h.missing_nominations > 0 && <span className="chip tabular-nums text-warn">{h.missing_nominations} no nomination</span>}
              {h.wills_to_check > 0 && <span className="chip tabular-nums text-warn">{h.wills_to_check} will to check</span>}
              {h.without_will > 0 && <span className="chip tabular-nums text-ink-muted">{h.without_will} no will on file</span>}
              {h.triggers.map((t: string) => <span key={t} className="chip text-info">{t}</span>)}
            </div>
          ))}
        </div>
      </div>
    );
  }
  const c = result.counts;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="estate-register-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <span className="text-sm font-medium text-ink">Asset register · {result.household}</span>
        <div className="flex flex-wrap gap-1.5">
          <span className="chip tabular-nums text-ink-muted">{c.assets} assets · {c.liabilities} debts</span>
          <span className={`chip tabular-nums ${c.missing_nominations ? "text-warn" : "text-ok"}`}>{c.missing_nominations} no nomination</span>
        </div>
      </div>
      <RegisterAssets view={result} limit={6} />
      <div className="border-t border-border-soft"><WillChecks view={result} /></div>
      <div className="space-y-1.5 border-t border-border-soft px-3 py-2">
        <TrustTriggers view={result} />
        {result.wishes.length > 0 && <div className="flex flex-wrap gap-1.5">{result.wishes.map((w: any) => <span key={w.id} className="chip text-ink-muted" title={w.words}>{w.kind_label} · {w.person}</span>)}</div>}
        <div className="flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">yearly check interval assumed</span>
          <span className="chip text-ink-muted">for the lawyer · not advice</span>
        </div>
      </div>
    </div>
  );
}
