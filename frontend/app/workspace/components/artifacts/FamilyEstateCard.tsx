"use client";

import { money } from "@/lib/format";

// India row 13: SEBI's family of client and the family's estate records, as a person recorded them. Figures and chips only; the
// agent's message carries the sentences and each source is a chip's title.

const DEP_TEXT: Record<string, string> = { spouse: "Spouse", child: "Child", parent: "Parent", none: "Not in the family" };

/** SEBI's families: who is in each, and whether it is recorded or the household stands in. */
export function FamilyChips({ families, relations }: { families: any[]; relations?: any[] }) {
  return (
    <div className="divide-y divide-border-soft" data-testid="sebi-families">
      {families.map((f) => (
        <div key={f.key} className="flex flex-wrap items-center gap-1.5 px-3 py-2" data-testid="sebi-family">
          <span className={`chip ${f.basis === "recorded" ? "text-ok" : "text-ink-muted"}`} title={f.stands_in ?? f.basis_note ?? ""}>
            {f.basis === "recorded" ? "Recorded" : "Household stands in"}
          </span>
          {f.members.map((m: any) => <span key={m.person_id} className="chip text-ink" title={m.household ?? ""}>{m.name}</span>)}
          {f.households?.length > 1 && <span className="chip text-ink-muted">{f.households.length} households</span>}
        </div>
      ))}
      {(relations ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 py-2" data-testid="sebi-relations">
          {relations!.map((r) => (
            <span key={r.id} className="chip text-ink-muted" title={r.document}>
              {r.person} · {DEP_TEXT[r.relation] ?? r.relation} of {r.of}{r.dependent === true ? " · dependent" : r.dependent === false ? " · not dependent" : ""}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

const ISSUE_TONE = "text-warn";

/** One household's estate records: nominations, each person's will and personal law, the HUF, gratuity. */
export function EstateHousehold({ view, currency }: { view: any; currency: string }) {
  const issues: any[] = view.nomination_issues ?? [];
  return (
    <div className="space-y-2 px-3 py-2" data-testid="estate-household">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-auto text-sm text-ink">{view.household}</span>
        {issues.length > 0 && <span className={`chip ${ISSUE_TONE}`}>{issues.length} nomination issue{issues.length === 1 ? "" : "s"}</span>}
      </div>
      {(view.nominations ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1.5" data-testid="estate-nominations">
          {view.nominations.map((n: any) => (
            <span key={n.id} className="chip text-ink-muted" title={n.document}>
              {n.person} · {n.asset_label}: {n.label} ·{" "}
              {n.status === "opted_out" ? `opted out ${n.declaration_on}` : n.nominees.map((x: any) => `${x.name}${x.share != null ? ` ${x.share}%` : ""}`).join(", ")}
            </span>
          ))}
        </div>
      )}
      {issues.length > 0 && (
        <div className="flex flex-wrap gap-1.5" data-testid="estate-issues">
          {issues.map((i, k) => <span key={k} className={`chip ${ISSUE_TONE}`}>{i.account}{i.name ? ` · ${i.name}` : ""} · {i.issue}</span>)}
        </div>
      )}
      {(view.people ?? []).filter((p: any) => p.status || p.will_on_file).map((p: any) => (
        <div key={p.person_id} className="flex flex-wrap items-center gap-1.5" data-testid="estate-person">
          <span className="text-xs text-ink-soft">{p.name}</span>
          <span className={`chip ${p.will_on_file ? "text-ink-muted" : "text-ink-muted"}`}>{p.will_on_file ? `Will ${p.will_signed_on}` : "No will on file"}</span>
          {p.status?.personal_law_label && <span className="chip text-ink-muted">{p.status.personal_law_label} law</span>}
          {p.status?.registered != null && <span className={`chip ${p.status.registered ? "text-ok" : "text-warn"}`} title={p.status.registration_ref ?? ""}>{p.status.registered ? "Registered" : "Not registered"}</span>}
          {p.status?.probate_label && <span className="chip text-ink-muted" title={p.status.probate_ref ?? ""}>Probate: {p.status.probate_label}</span>}
          {p.status?.executor && <span className="chip text-ink-muted">Executor {p.status.executor}</span>}
          {p.status?.executor_told != null && <span className={`chip ${p.status.executor_told ? "text-ok" : "text-warn"}`}>Executor {p.status.executor_told ? "told" : "not told"}</span>}
          {p.status?.witnesses?.length ? <span className={`chip ${p.status.witnesses.length >= 2 ? "text-ok" : "text-warn"}`} title={p.status.witnesses.join(", ")}>{p.status.witnesses.length} witness{p.status.witnesses.length === 1 ? "" : "es"}</span> : null}
          {p.succession?.state === "not_assessed" && <span className="chip text-ink-muted">Succession not assessed</span>}
          {p.succession?.state === "laid_out" && (
            <span className="chip text-ink-muted" title={(p.succession.orders ?? []).map((o: any) => `${o.title}: ${o.figure.source}`).join("\n")}>Intestacy laid out, for counsel</span>
          )}
          {p.succession?.state === "not_laid_out" && <span className="chip text-ink-muted">Succession not laid out</span>}
          {(p.flags ?? []).map((f: string) => <span key={f} className="chip text-warn">{f}</span>)}
        </div>
      ))}
      {(view.hufs ?? []).map((h: any) => (
        <div key={h.entity_id} className="flex flex-wrap items-center gap-1.5" data-testid="estate-huf">
          <span className="text-xs text-ink-soft">{h.name}</span>
          {h.recorded
            ? <><span className="chip text-ink-muted" title={h.document ?? ""}>Karta {h.karta}</span>
                <span className="chip text-ink-muted">{(h.coparceners ?? []).length} coparcener{(h.coparceners ?? []).length === 1 ? "" : "s"}</span></>
            : <span className="chip text-ink-muted">Karta not recorded</span>}
          {(h.flags ?? []).filter((f: string) => !f.startsWith("no karta")).map((f: string) => <span key={f} className="chip text-warn">{f}</span>)}
        </div>
      ))}
      {(view.gratuity ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1.5" data-testid="estate-gratuity">
          {view.gratuity.map((g: any) => (
            <span key={`${g.person_id}-${g.employer}`} className="chip tabular-nums text-ink-muted" title={g.document}>
              Gratuity {g.person} · {money(g.amount, currency)} · statement {g.as_of}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** SEBI's family of client, or a household's estate records, or what recording one did. */
export function FamilyEstateCard({ result }: { result: any }) {
  if (result.applies === false) {
    return <div className="rounded-lg border border-border bg-surface px-3 py-2" data-testid="family-estate-card"><span className="chip text-ink-muted">Not an Indian firm</span></div>;
  }
  const currency = result.currency ?? "INR";
  const households: any[] = Array.isArray(result.households) ? result.households : result.nominations ? [result] : [];
  const families: any[] | null = Array.isArray(result.families) ? result.families : result.family?.families ?? null;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="family-estate-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{households.length ? "Family and estate" : "SEBI family of client"}</div>
        <div className="flex flex-wrap gap-1.5">
          {result.recorded != null && <span className="chip text-ok">{result.recorded} recorded</span>}
          {result.household_standing_in != null && <span className="chip text-ink-muted">{result.household_standing_in} household stands in</span>}
          {result.issues > 0 && <span className="chip text-warn">{result.issues} to look at</span>}
        </div>
      </div>
      {families && <FamilyChips families={families} relations={result.relations} />}
      {households.map((h) => <EstateHousehold key={h.household_id} view={h} currency={currency} />)}
    </div>
  );
}
