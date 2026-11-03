"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import {
  ArrowRight, CalendarPlus, ClipboardList, Download, FileText, HelpCircle, Leaf, MessageSquare, Sprout, Target,
} from "lucide-react";
import { AllocationDonut } from "@/components/Charts";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import MessageThread from "@/components/MessageThread";
import { API_BASE, api, getToken, getUser } from "@/lib/api";
import { money, pct, titleCase } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { ViewLink } from "./ViewNav";
import { AllClients, ViewHeader } from "./ViewHeader";

const ROUTE = "/canvas";

/** Every household, each a link to its own page of a canvas view. Staff only: a client has one household. */
export function CanvasHouseholdPicker({ title, route, testId }: { title: string; route: string; testId: string }) {
  const { data, loading, error, refetch } = useApi<any[]>("/api/core/households");
  if (error) return <Card><ErrorState what="households" message={error} onRetry={refetch} /></Card>;
  if (loading || !data) return <SkeletonList count={4} />;
  return (
    <>
      <ViewHeader title={title} meta={<span className="chip text-ink-muted">{data.length} households</span>} />
      {data.length === 0 ? <Empty>No households.</Empty> : (
        <Card className="overflow-hidden p-0">
          <ul className="divide-y divide-border-soft" data-testid={testId}>
            {data.map((h: any) => (
              <li key={h.id}>
                <ViewLink href={`${route}?household=${h.id}`} data-testid="canvas-household"
                  className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 hover:bg-border-soft">
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">{h.name}</span>
                  {h.segment && <span className="chip text-ink-muted">{titleCase(h.segment)}</span>}
                  <span className="font-mono text-sm tabular-nums text-ink-muted">{money(h.total_value)}</span>
                </ViewLink>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

/** The client's own page of their wealth: what they hold, their goals, what is on file, their reports
 * and fees. A client sees their own household whatever is asked for; staff open one client's page, read
 * only, with every change going through the conversation or the adviser's own views. */
export function CanvasView({ household = null }: { household?: string | null }) {
  const isClient = getUser()?.role === "client";
  if (isClient) return <Page householdId={null} />;
  return household ? <Page householdId={household} /> : <div className="view-container"><CanvasHouseholdPicker title="Client canvas" route={ROUTE} testId="canvas-households" /></div>;
}

/** A file from an authenticated GET, saved under `name`. A read, so staff may take one too. */
function download(path: string, name: string) {
  fetch(`${API_BASE}${path}`, { headers: { Authorization: `Bearer ${getToken()}` } })
    .then((r) => r.blob())
    .then((blob) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      a.click();
    })
    .catch(() => {});
}

function Tile({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-border-soft p-3" data-testid={testId}>
      <div className="tile-label">{label}</div>
      <div className="mt-1">{children}</div>
    </div>
  );
}

const Figure = ({ children, tone = "text-ink" }: { children: ReactNode; tone?: string }) => (
  <div className={`truncate font-mono text-lg tabular-nums ${tone}`}>{children}</div>
);

/** `householdId` null is the signed-in client's own household. */
function Page({ householdId }: { householdId: string | null }) {
  const client = householdId === null;
  const qs = householdId ? `?household_id=${householdId}` : "";
  const me = useApi<any>(`/api/canvas/me${qs}`, [qs]);
  const sheet = useApi<any>(`/api/canvas/net-worth${qs}`, [qs]);
  const terms = useApi<any>(`/api/canvas/ips${qs}`, [qs]);

  const [requestBusy, setRequestBusy] = useState(false);
  const [requestDone, setRequestDone] = useState(false);
  const [correction, setCorrection] = useState("");
  const [docs, setDocs] = useState<any[] | null>(null);
  const [docsError, setDocsError] = useState<string | null>(null);
  const [fees, setFees] = useState<any>(null);
  const [feesError, setFeesError] = useState<string | null>(null);

  const view = me.data;
  if (me.error) return <div className="view-container"><Card><ErrorState what="the canvas" message={me.error} onRetry={me.refetch} /></Card></div>;
  if (me.loading || !view || (householdId && view.household_id !== householdId)) return <div className="view-container"><SkeletonList count={4} /></div>;

  const worth = sheet.data && (!householdId || sheet.data.household_id === householdId) ? sheet.data : null;
  const policy = terms.data && (!householdId || terms.data.household_id === householdId) ? terms.data : null;
  const ccy: string | undefined = view.currency || undefined;
  const name: string = view.household?.name ?? "";
  const accent: string | undefined = view.firm?.branding?.accent || undefined;
  const goals: any[] = view.goals ?? [];
  const nextGen: any[] = view.next_gen ?? [];
  const reports: any[] = view.reports ?? [];
  const hidQs = householdId ? `?household=${householdId}` : "";

  // Client only: the client's own requests to their adviser. Staff never reach these.
  async function sendRequest(type: string, notes = "") {
    if (!client) return;
    setRequestBusy(true);
    try {
      await api("/api/canvas/requests", { body: { request_type: type, notes } });
      setRequestDone(true);
      setTimeout(() => setRequestDone(false), 3000);
    } catch {}
    finally { setRequestBusy(false); }
  }

  async function loadDocs() {
    setDocsError(null);
    try { setDocs(await api<any[]>(`/api/canvas/documents${qs}`)); }
    catch (e: any) { setDocsError(e?.message || "Failed to load"); }
  }

  async function loadFees() {
    setFeesError(null);
    try { setFees(await api<any>(`/api/canvas/fees${qs}`)); }
    catch (e: any) { setFeesError(e?.message || "Failed to load"); }
  }

  const adviserFirst = view.adviser?.name?.split(" ")[0];
  const stress = view.stress;

  return (
    <div className="view-container" data-testid="canvas-view">
      <ViewHeader
        title={client ? "My wealth" : `Canvas · ${name}`}
        meta={<>
          {view.firm?.name && <span className="chip text-ink-muted">{view.firm.name}</span>}
          {!client && <span className="chip text-ink-muted">client&rsquo;s view</span>}
          {view.as_of && <span className="chip text-ink-muted">as of {view.as_of}</span>}
        </>}
        actions={client ? undefined : <AllClients href={ROUTE} />}
      />

      {/* The firm's identity for its client is a thin stripe, nothing more. */}
      <Card className="mb-4 border-l-4" style={accent ? { borderLeftColor: accent } : undefined} data-testid="canvas-hero">
        <div className="grid grid-cols-1 gap-3 cq-sm:grid-cols-3">
          <div className="min-w-0">
            <div className="tile-label">Managed wealth</div>
            <div className="truncate font-mono text-2xl tabular-nums text-ink" data-testid="canvas-total">{money(view.total_wealth, ccy)}</div>
          </div>
          <div className="min-w-0">
            <div className="tile-label">Goals on track</div>
            {goals.length ? (
              <div className={`font-mono text-2xl tabular-nums ${view.goals_on_track === goals.length ? "text-ok" : "text-warn"}`}>
                {view.goals_on_track} of {goals.length}
              </div>
            ) : <span className="chip mt-1 text-ink-muted">no goals on file</span>}
          </div>
          <div className="min-w-0">
            <div className="tile-label">Adviser</div>
            {view.adviser ? (
              <>
                <div className="truncate text-sm text-ink">{view.adviser.name}</div>
                {view.adviser.title && <div className="truncate text-xs text-ink-muted">{view.adviser.title}</div>}
              </>
            ) : <span className="chip mt-1 text-ink-muted">not on file</span>}
          </div>
        </div>
      </Card>

      {nextGen.length > 0 && (
        <ViewLink href={client ? "/canvas/next-gen" : `/canvas/next-gen${hidQs}`} data-testid="canvas-next-gen"
          className="card mb-4 flex items-center gap-3 p-4 transition hover:bg-border-soft">
          <Sprout size={18} className="shrink-0 text-ok" aria-hidden />
          <span className="text-sm font-medium text-ink">Next-gen journey</span>
          <span className="flex min-w-0 flex-1 flex-wrap gap-1.5">
            {nextGen.map((p: any) => <span key={p.id} className="chip bg-border-soft text-ink-soft">{p.name}</span>)}
          </span>
          <ArrowRight size={16} className="shrink-0 text-ink-muted" aria-hidden />
        </ViewLink>
      )}

      <div className="mb-4 grid grid-cols-1 gap-4 cq-md:grid-cols-2">
        <Card data-testid="canvas-goals">
          <div className="mb-3 flex items-center justify-between gap-2">
            <span className="text-sm font-medium text-ink">Goals</span>
            <span className="chip text-ink-muted">estimate</span>
          </div>
          {goals.length === 0 ? <span className="chip text-ink-muted">none on file</span> : (
            <div className="space-y-4">
              {goals.map((g: any, i: number) => (
                <div key={i}>
                  <div className="flex items-baseline justify-between gap-2 text-sm">
                    <span className="min-w-0 truncate text-ink-soft">{g.name}</span>
                    <span className={`chip shrink-0 ${g.on_track ? "text-ok" : "text-warn"}`}>{g.on_track ? "on track" : "off track"}</span>
                  </div>
                  <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-border-soft">
                    <div className={`h-full rounded-full ${g.on_track ? "bg-ok" : "bg-warn"}`} style={{ width: `${Math.max(0, Math.min(100, (g.probability ?? 0) * 100))}%` }} />
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    <span className="chip text-ink-muted">likelihood {pct(g.probability, 0)}</span>
                    <span className="chip text-ink-muted">target {money(g.target_amount, ccy)}</span>
                    {g.projected_median != null && <span className="chip text-ink-muted">median {money(g.projected_median, ccy)}</span>}
                    {g.assumed?.includes("years") && <span className="chip text-warn">assumed {g.horizon_years}-year horizon</span>}
                        </div>
                </div>
              ))}
            </div>
          )}
          {stress && (
            <div className="mt-4 border-t border-border-soft pt-3" data-testid="canvas-stress">
              <div className="tile-label">COVID-style shock</div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-lg tabular-nums text-crit">−{pct(Math.abs(stress.impact_pct ?? 0), 0)}</span>
                <span className="chip text-ink-muted">scenario estimate</span>
              </div>
            </div>
          )}
        </Card>

        <Card data-testid="canvas-allocation">
          <div className="mb-3 text-sm font-medium text-ink">How it is invested</div>
          {view.allocation && Object.keys(view.allocation).length > 0
            ? <AllocationDonut data={view.allocation} size={150} />
            : <span className="chip text-ink-muted">nothing held</span>}
          {view.values?.themes?.length > 0 && (
            <div className="mt-4 border-t border-border-soft pt-3">
              <div className="tile-label mb-1.5 flex items-center gap-1"><Leaf size={12} aria-hidden /> Values</div>
              <div className="flex flex-wrap gap-1.5">
                {view.values.themes.map((t: string) => <span key={t} className="chip bg-ok/10 text-ok">{titleCase(t)}</span>)}
              </div>
            </div>
          )}
        </Card>
      </div>

      {(worth || policy) && (
        <div className="mb-4 grid grid-cols-1 gap-4 cq-md:grid-cols-2">
          {worth && (
            <Card data-testid="canvas-net-worth">
              <div className="mb-3 flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-ink">Own and owe</span>
                <span className="font-mono text-lg tabular-nums text-ink">{money(worth.net_worth, worth.currency)}</span>
              </div>
              <div className="space-y-1.5 text-sm">
                <div className="flex justify-between gap-2"><span className="text-ink-soft">Managed here</span><span className="font-mono tabular-nums text-ink">{money(worth.assets?.managed, worth.currency)}</span></div>
                {(worth.items ?? []).filter((i: any) => i.side !== "expense").map((i: any) => (
                  <div key={i.id} className="flex justify-between gap-2">
                    <span className="min-w-0 truncate text-ink-soft">{i.name}</span>
                    <span className={`font-mono tabular-nums ${i.side === "liability" ? "text-warn" : "text-ink"}`}>{i.side === "liability" ? "−" : ""}{money(i.amount, i.currency || worth.currency)}</span>
                  </div>
                ))}
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {worth.basis === "assets_only" && <span className="chip text-ink-muted">no debts on file</span>}
                {worth.not_converted?.length > 0 && <span className="chip text-ink-muted">{worth.not_converted.length} in another currency, not added</span>}
              </div>
            </Card>
          )}
          {policy && (
            <Card data-testid="canvas-ips">
              <div className="mb-3 text-sm font-medium text-ink">Investment policy</div>
              {policy.policies.length === 0 ? <span className="chip text-ink-muted">not on file here</span> : (
                <div className="space-y-3">
                  {policy.policies.map((p: any) => (
                    <div key={p.mandate} className="text-sm">
                      <div className="flex flex-wrap items-center gap-1.5 text-ink-soft">
                        <span>{p.mandate}</span>
                        <span className="chip text-ink-muted">version {p.version}</span>
                        <span className="chip text-ink-muted">accepted {p.accepted_on}</span>
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {Object.entries(p.targets ?? {}).map(([k, v]: any) => <span key={k} className="chip bg-border-soft text-ink-soft">{titleCase(k.replace(/_/g, " "))} {pct(v, 0)}</span>)}
                        {p.max_position != null && <span className="chip text-ink-muted">largest holding {pct(p.max_position, 0)}</span>}
                        {p.min_cash != null && <span className="chip text-ink-muted">cash floor {pct(p.min_cash, 0)}</span>}
                        {(p.restricted ?? []).map((r: string) => <span key={r} className="chip text-ink-muted">not {r}</span>)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )}
        </div>
      )}
      {client && (worth || policy) && (
        <form className="card mb-4 flex flex-wrap gap-2 p-4 cq-sm:flex-nowrap" data-testid="canvas-correction"
          onSubmit={(e) => { e.preventDefault(); sendRequest("correction", correction); setCorrection(""); }}>
          <input className="input" aria-label="What is wrong" placeholder="Something here is wrong" value={correction} onChange={(e) => setCorrection(e.target.value)} />
          <button className="btn-outline shrink-0 text-sm" disabled={requestBusy || correction.trim().length < 3}>Tell my adviser</button>
        </form>
      )}

      <div className="mb-4 grid grid-cols-1 gap-4 cq-md:grid-cols-2">
        {client ? (
          <Card className="flex flex-col" data-testid="canvas-messages">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-ink">
              <MessageSquare size={16} className="text-ink-muted" aria-hidden /> Messages{adviserFirst ? ` · ${adviserFirst}` : ""}
              {view.unread_messages > 0 && <span className="chip bg-border-soft text-ink">{view.unread_messages} unread</span>}
            </div>
            <MessageThread />
          </Card>
        ) : (
          <Card data-testid="canvas-messages">
            <ViewLink href={`/studio/messages/${householdId}`} data-testid="canvas-messages-link"
              className="flex items-center gap-2 rounded-xl px-1 py-1 text-sm font-medium text-ink hover:bg-border-soft">
              <MessageSquare size={16} className="text-ink-muted" aria-hidden /> Messages
              <span className={`chip ${view.unread_messages > 0 ? "bg-border-soft text-ink" : "text-ink-muted"}`}>{view.unread_messages ?? 0} unread by client</span>
              <ArrowRight size={15} className="ml-auto text-ink-muted" aria-hidden />
            </ViewLink>
          </Card>
        )}
        <Card data-testid="canvas-reports">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-ink"><FileText size={16} className="text-ink-muted" aria-hidden /> Reports</div>
          {reports.length === 0 ? <span className="chip text-ink-muted">none published</span> : (
            <ul className="divide-y divide-border-soft">
              {reports.map((r: any) => {
                const inner = (
                  <>
                    <FileText size={15} className="shrink-0 text-ink-muted" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-ink">{r.title}</span>
                      <span className="block text-xs text-ink-muted">{r.period}</span>
                    </span>
                    <ArrowRight size={15} className="shrink-0 text-ink-muted" aria-hidden />
                  </>
                );
                const cls = "flex items-center gap-3 rounded-lg px-1 py-2 hover:bg-border-soft";
                return (
                  <li key={r.id}>
                    {client
                      ? <Link href={`/canvas/report/${r.id}`} className={cls}>{inner}</Link>
                      : <ViewLink href={`/studio/reports/${r.id}`} className={cls}>{inner}</ViewLink>}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>

      {client && (
        <Card className="mb-4" data-testid="canvas-requests">
          <div className="mb-3 text-sm font-medium text-ink">Requests</div>
          {requestDone ? <span className="chip bg-ok/10 text-ok">sent to your adviser</span> : (
            <div className="flex flex-wrap gap-2">
              <button className="btn-outline text-sm" disabled={requestBusy} onClick={() => sendRequest("meeting")}><CalendarPlus size={15} aria-hidden /> Request a meeting</button>
              <button className="btn-outline text-sm" disabled={requestBusy} onClick={() => sendRequest("goal_update")}><Target size={15} aria-hidden /> Update my goals</button>
              <button className="btn-outline text-sm" disabled={requestBusy} onClick={() => sendRequest("query")}><HelpCircle size={15} aria-hidden /> I have a question</button>
            </div>
          )}
        </Card>
      )}

      <Card className="mb-4" data-testid="canvas-documents">
        <div className="mb-3 flex items-center justify-between gap-2">
          <span className="flex items-center gap-2 text-sm font-medium text-ink"><FileText size={16} className="text-ink-muted" aria-hidden /> Documents</span>
          {docs === null && <button className="btn-ghost px-2 py-1 text-xs" onClick={loadDocs} data-testid="canvas-documents-load">Load</button>}
        </div>
        {docsError && <ErrorState what="documents" message={docsError} onRetry={loadDocs} />}
        {docs && (docs.length === 0 ? <span className="chip text-ink-muted">none shared</span> : (
          <ul className="divide-y divide-border-soft">
            {docs.map((d: any) => (
              <li key={d.id} className="flex items-center gap-3 py-2">
                <FileText size={15} className="shrink-0 text-ink-muted" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-ink">{d.filename}</span>
                  <span className="block text-xs text-ink-muted">{titleCase(d.doc_type ?? "")}{d.created_at ? ` · ${new Date(d.created_at).toLocaleDateString()}` : ""}</span>
                </span>
                {d.size_bytes > 0 && <span className="shrink-0 font-mono text-xs tabular-nums text-ink-muted">{(d.size_bytes / 1024).toFixed(0)} KB</span>}
              </li>
            ))}
          </ul>
        ))}
      </Card>

      <div className="mb-4 flex flex-wrap gap-2">
        <button onClick={() => download(`/api/canvas/summary/pdf${qs}`, "Wealth_Summary.pdf")} className="btn-outline text-sm" data-testid="canvas-summary-pdf">
          <Download size={14} aria-hidden /> Wealth summary PDF
        </button>
        {client
          ? <Link href="/canvas/questionnaire" className="btn-outline text-sm" data-testid="canvas-questionnaire"><ClipboardList size={14} aria-hidden /> Risk questionnaire</Link>
          : <ViewLink href={`/canvas/questionnaire${hidQs}`} className="btn-outline text-sm" data-testid="canvas-questionnaire"><ClipboardList size={14} aria-hidden /> Risk questionnaire</ViewLink>}
      </div>

      <Card data-testid="canvas-fees">
        <div className="mb-3 flex items-center justify-between gap-2">
          <span className="text-sm font-medium text-ink">Fees</span>
          {fees === null && <button className="btn-ghost px-2 py-1 text-xs" onClick={loadFees} data-testid="canvas-fees-load">Load</button>}
        </div>
        {feesError && <ErrorState what="fees" message={feesError} onRetry={loadFees} />}
        {fees && <Fees fees={fees} qs={qs} />}
      </Card>
    </div>
  );
}

const SOURCE: Record<string, string> = {
  confirmed_fee_schedule: "confirmed schedule",
  segment_estimate: "segment estimate",
};

function Fees({ fees, qs }: { fees: any; qs: string }) {
  const ccy: string | undefined = fees.currency || undefined;
  const onFile = fees.source !== "not_on_file";
  const notOnFile = <span className="chip text-ink-muted" data-testid="fees-not-on-file">not on file</span>;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 cq-sm:grid-cols-3">
        <Tile label="Annual rate">
          {onFile && fees.fee_pct != null ? (
            <>
              <Figure>{Number(fees.fee_pct).toFixed(2)}%</Figure>
              <span className="chip mt-1 text-ink-muted">{SOURCE[fees.source] ?? titleCase(fees.source)}</span>
            </>
          ) : notOnFile}
        </Tile>
        <Tile label="Annual fee">
          {onFile && fees.annual_fee != null ? (
            <>
              <Figure>{money(fees.annual_fee, ccy)}</Figure>
              <span className="chip mt-1 text-ink-muted">estimate</span>
            </>
          ) : notOnFile}
        </Tile>
        <Tile label={fees.billed_ytd != null ? "Billed this year" : "Year to date"} testId="fees-billed-ytd">
          {fees.billed_ytd != null ? <Figure>{money(fees.billed_ytd, ccy)}</Figure>
            : onFile && fees.ytd_fee != null ? (
              <>
                <Figure>{money(fees.ytd_fee, ccy)}</Figure>
                <span className="chip mt-1 text-ink-muted">estimate · nothing billed</span>
              </>
            ) : <span className="chip text-ink-muted">nothing billed</span>}
        </Tile>
      </div>

      {fees.invoices?.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs" data-testid="fees-invoices">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted">
                <th className="pb-1 pr-3 font-medium">Invoice</th><th className="pb-1 pr-3 font-medium">Period</th>
                <th className="pb-1 text-right font-medium">Amount</th><th className="pb-1 pl-3 font-medium">Status</th><th />
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {fees.invoices.map((i: any) => (
                <tr key={i.id}>
                  <td className="py-1.5 pr-3 text-ink">{i.number}{i.kind === "credit_note" ? " · credit" : ""}</td>
                  <td className="py-1.5 pr-3 font-mono text-ink-muted">{i.period_start} → {i.period_end}</td>
                  <td className="py-1.5 text-right font-mono tabular-nums text-ink">{money(i.amount, i.currency || ccy, 2)}</td>
                  <td className="py-1.5 pl-3">
                    <span className={`chip ${i.status === "credited" || i.kind === "credit_note" ? "text-ink-muted" : i.paid ? "text-ok" : "text-warn"}`}>
                      {i.status === "credited" ? "credited" : i.kind === "credit_note" ? "credit applied" : i.paid ? "paid" : "unpaid"}
                    </span>
                  </td>
                  <td className="py-1.5 pl-3 text-right">
                    <button className="btn-ghost px-2 py-0.5 text-xs" onClick={() => download(`/api/canvas/invoices/${i.id}/pdf${qs}`, `${i.number}.pdf`)}>PDF</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {fees.applicable_segment && (
        <div className="flex flex-wrap gap-1.5">
          <span className="chip bg-border-soft text-ink-soft">{fees.applicable_segment.label}</span>
          <span className="chip text-ink-muted">{fees.applicable_segment.fee_bps} bps</span>
          <span className="chip text-ink-muted">on {money(fees.aum, ccy)}</span>
        </div>
      )}

      {fees.fee_schedule?.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-ink-muted hover:text-ink">Fee schedule</summary>
          <table className="mt-2 w-full">
            <thead><tr className="text-left text-[10px] uppercase tracking-wide text-ink-muted"><th className="pb-1 font-medium">Segment</th><th className="pb-1 font-medium">From</th><th className="pb-1 font-medium">Rate</th></tr></thead>
            <tbody className="divide-y divide-border-soft">
              {fees.fee_schedule.map((s: any) => (
                <tr key={s.slug}>
                  <td className="py-1 pr-4 text-ink">{s.label}</td>
                  <td className="py-1 pr-4 font-mono text-ink-muted">{s.min_aum ? money(s.min_aum, ccy) : "any"}</td>
                  <td className="py-1 font-mono text-ink-muted">{Number(s.fee_pct).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}
