"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Send, ShieldCheck, Heart, Leaf, Mic, MessageSquare, FileText, Sprout, ArrowRight, Volume2, CalendarPlus, Target, HelpCircle, Download, ClipboardList, DollarSign } from "lucide-react";
import { AllocationDonut } from "@/components/Charts";
import { Spinner } from "@/components/ui";
import MessageThread from "@/components/MessageThread";
import { api, getUser } from "@/lib/api";
import { money, pct, titleCase } from "@/lib/format";
import { listenOnce, speak, voiceSupported } from "@/lib/voice";

export default function Canvas() {
  const user = typeof window !== "undefined" ? getUser() : null;
  const isClient = user?.role === "client";
  const [households, setHouseholds] = useState<any[]>([]);
  const [hid, setHid] = useState<string | null>(null);
  const [view, setView] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [thread, setThread] = useState<any[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [requestBusy, setRequestBusy] = useState(false);
  const [requestDone, setRequestDone] = useState(false);
  const [docs, setDocs] = useState<any[]>([]);
  const [docsLoaded, setDocsLoaded] = useState(false);
  const [fees, setFees] = useState<any>(null);
  const [feesLoaded, setFeesLoaded] = useState(false);

  // Staff: choose a household to preview. Client: their own.
  useEffect(() => {
    (async () => {
      if (!isClient) {
        const hh = await api("/api/core/households");
        setHouseholds(hh);
        setHid(hh[0]?.id || null);
      } else {
        load(null);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isClient && hid) load(hid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hid]);

  async function load(householdId: string | null) {
    setLoading(true);
    setThread([]);
    try {
      const path = householdId ? `/api/canvas/me?household_id=${householdId}` : "/api/canvas/me";
      setView(await api(path));
    } finally {
      setLoading(false);
    }
  }

  async function ask(question: string, viaVoice = false) {
    if (!question.trim()) return;
    setBusy(true);
    setThread((t) => [...t, { role: "user", text: question }]);
    setQ("");
    try {
      const path = !isClient && hid ? `/api/canvas/assistant?household_id=${hid}` : "/api/canvas/assistant";
      const res = await api(path, { body: { question } });
      setThread((t) => [...t, { role: "assistant", ...res }]);
      if (viaVoice) speak(res.answer);
    } catch (e: any) {
      setThread((t) => [...t, { role: "assistant", answer: e.message, error: true }]);
    } finally {
      setBusy(false);
    }
  }

  async function sendRequest(type: string) {
    setRequestBusy(true);
    try {
      const body: any = { request_type: type };
      if (!isClient && hid) body.household_id = hid;
      await api("/api/canvas/requests", { body });
      setRequestDone(true);
      setTimeout(() => setRequestDone(false), 3000);
    } catch {}
    finally { setRequestBusy(false); }
  }

  async function loadDocs() {
    try {
      const path = !isClient && hid ? `/api/canvas/documents?household_id=${hid}` : "/api/canvas/documents";
      const data = await api<any[]>(path);
      setDocs(data);
    } catch {}
    setDocsLoaded(true);
  }

  async function loadFees() {
    try {
      const path = !isClient && hid ? `/api/canvas/fees?household_id=${hid}` : "/api/canvas/fees";
      const data = await api<any>(path);
      setFees(data);
    } catch {}
    setFeesLoaded(true);
  }

  function downloadPdf() {
    const { getToken } = require("@/lib/api");
    const token = getToken();
    const base = process.env.NEXT_PUBLIC_API_URL || "";
    const qs = !isClient && hid ? `?household_id=${hid}` : "";
    const url = `${base}/api/canvas/summary/pdf${qs}`;
    // Fetch with auth header and trigger download
    fetch(url, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.blob())
      .then(blob => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "Wealth_Summary.pdf";
        a.click();
      })
      .catch(() => {});
  }

  function startVoice() {
    setListening(true);
    listenOnce((text) => { setQ(text); ask(text, true); }, () => setListening(false));
  }

  if (loading || !view) return <Spinner label="Loading your wealth view…" />;

  const branding = view.firm?.branding || {};
  const accent = branding.accent || "#c8a35e";
  const onTrack = view.goals?.filter((g: any) => g.on_track).length || 0;

  return (
    <div className="max-w-4xl mx-auto">
      {/* Staff preview selector */}
      {!isClient && (
        <div className="mb-4 flex items-center gap-2 p-2 rounded-xl bg-gold-soft/10 border border-gold/20">
          <span className="text-xs text-gold-dark font-medium shrink-0">Adviser preview:</span>
          <div className="flex flex-wrap gap-2">
            {households.map((h) => (
              <button
                key={h.id}
                onClick={() => setHid(h.id)}
                className={`chip text-xs transition ${hid === h.id ? "bg-navy-800 text-white" : "bg-white border border-navy-100 text-ink-soft hover:border-navy-300"}`}
              >
                {h.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Hero */}
      <div className="rounded-2xl p-8 text-white relative overflow-hidden mb-5" style={{ background: "#0f2b3d" }}>
        <div className="absolute inset-0 opacity-30" style={{ background: `radial-gradient(600px circle at 85% 15%, ${accent}55, transparent 60%)` }} />
        <div className="relative">
          <div className="text-sm text-white/60">{view.firm?.name}</div>
          <h1 className="font-serif text-3xl mt-2">{view.headline}</h1>
          <p className="text-white/70 mt-2">
            Your total wealth is <span className="font-semibold text-white">{money(view.total_wealth)}</span>
            {view.goals?.length ? <> · {onTrack} of {view.goals.length} goals on track</> : null}.
          </p>
          {view.adviser && (
            <div className="mt-5 flex items-center gap-2 text-sm text-white/80">
              <Heart size={15} style={{ color: accent }} />
              Looked after by {view.adviser.name}{view.adviser.title ? `, ${view.adviser.title}` : ""}
            </div>
          )}
        </div>
      </div>

      {/* Next-gen journey invite */}
      {view.next_gen?.length > 0 && (
        <Link href={`/canvas/next-gen${!isClient ? `?person_id=${view.next_gen[0].id}` : ""}`}
              className="card p-4 mb-5 flex items-center gap-3 hover:shadow-lift transition group border-gold/30">
          <div className="h-10 w-10 rounded-xl bg-gold-soft/40 text-gold-dark flex items-center justify-center"><Sprout size={18} /></div>
          <div className="flex-1">
            <div className="font-medium text-ink">Next-gen journey for {view.next_gen[0].name}</div>
            <div className="text-xs text-ink-muted">An education-led, digital-first space to learn about the family's wealth.</div>
          </div>
          <ArrowRight className="text-gold group-hover:translate-x-0.5 transition" size={18} />
        </Link>
      )}

      <div className="grid md:grid-cols-2 gap-5">
        {/* Goals */}
        <div className="card p-5">
          <div className="font-semibold text-ink mb-3">Am I on track?</div>
          <div className="space-y-4">
            {view.goals?.map((g: any, i: number) => (
              <div key={i}>
                <div className="flex justify-between text-sm">
                  <span className="text-ink-soft">{g.name}</span>
                  <span className={g.on_track ? "text-positive font-medium" : "text-caution font-medium"}>
                    {g.on_track ? "On track" : "Let's talk"}
                  </span>
                </div>
                <div className="h-2.5 rounded-full bg-navy-100 mt-1.5 overflow-hidden">
                  <div className={`h-full ${g.on_track ? "bg-positive" : "bg-caution"}`} style={{ width: `${Math.min(100, g.probability * 100)}%` }} />
                </div>
                <div className="text-xs text-ink-muted mt-1">
                  {pct(g.probability, 0)} likelihood · target {money(g.target_amount)}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Allocation + values */}
        <div className="card p-5">
          <div className="font-semibold text-ink mb-3">How your wealth is invested</div>
          <AllocationDonut data={view.allocation} size={150} />
          {view.values?.themes?.length > 0 && (
            <div className="mt-4 pt-3 border-t border-navy-100">
              <div className="text-xs text-ink-muted mb-1.5 flex items-center gap-1"><Leaf size={12} /> Aligned to your values</div>
              <div className="flex flex-wrap gap-1.5">
                {view.values.themes.map((t: string) => (
                  <span key={t} className="chip bg-positive/10 text-positive">{titleCase(t)}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Messages + Reports */}
      <div className="grid md:grid-cols-2 gap-5 mt-5">
        <div className="card p-5 flex flex-col">
          <div className="font-semibold text-ink mb-3 flex items-center gap-2">
            <MessageSquare size={17} className="text-navy-600" /> Messages with {view.adviser?.name?.split(" ")[0] || "your adviser"}
          </div>
          <MessageThread householdId={!isClient ? hid || undefined : undefined} />
        </div>
        <div className="card p-5">
          <div className="font-semibold text-ink mb-3 flex items-center gap-2"><FileText size={17} className="text-navy-600" /> Your reports</div>
          {view.reports?.length ? (
            <div className="space-y-2">
              {view.reports.map((r: any) => (
                <Link key={r.id} href={`/canvas/report/${r.id}`} className="rounded-xl border border-navy-100 p-3 flex items-center gap-3 hover:border-navy-300 transition">
                  <FileText size={16} className="text-gold" />
                  <div className="flex-1">
                    <div className="text-sm font-medium text-ink">{r.title}</div>
                    <div className="text-xs text-ink-muted">{r.period}</div>
                  </div>
                  <ArrowRight size={15} className="text-navy-300" />
                </Link>
              ))}
            </div>
          ) : (
            <div className="text-sm text-ink-muted">No reports published yet. Your adviser will share these here.</div>
          )}
        </div>
      </div>

      {/* Client-initiated actions */}
      {isClient && (
        <div className="card p-5 mt-5">
          <div className="font-semibold text-ink mb-3">Take action</div>
          {requestDone ? (
            <div className="text-sm text-positive flex items-center gap-2">
              <ShieldCheck size={15} /> Your request has been sent to your adviser.
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <button className="btn-outline text-sm" disabled={requestBusy} onClick={() => sendRequest("meeting")}>
                <CalendarPlus size={15} /> Request a meeting
              </button>
              <button className="btn-outline text-sm" disabled={requestBusy} onClick={() => sendRequest("goal_update")}>
                <Target size={15} /> Update my goals
              </button>
              <button className="btn-outline text-sm" disabled={requestBusy} onClick={() => sendRequest("query")}>
                <HelpCircle size={15} /> I have a question
              </button>
            </div>
          )}
        </div>
      )}

      {/* Document vault */}
      <div className="card p-5 mt-5">
        <div className="font-semibold text-ink mb-3 flex items-center justify-between">
          <span className="flex items-center gap-2"><FileText size={17} className="text-navy-600" /> Documents</span>
          {!docsLoaded && <button className="text-xs text-ink-muted hover:text-ink underline" onClick={loadDocs}>Load</button>}
        </div>
        {docsLoaded && (
          docs.length === 0 ? (
            <div className="text-sm text-ink-muted">No documents shared yet.</div>
          ) : (
            <div className="space-y-2">
              {docs.map((d: any) => (
                <div key={d.id} className="flex items-center gap-3 px-3 py-2.5 rounded-xl border border-navy-100 hover:border-navy-300 transition">
                  <FileText size={16} className="text-gold shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-ink truncate">{d.filename}</div>
                    <div className="text-xs text-ink-muted">{d.doc_type} · {d.created_at ? new Date(d.created_at).toLocaleDateString() : ""}</div>
                  </div>
                  {d.size_bytes > 0 && <div className="text-xs text-ink-muted shrink-0">{(d.size_bytes / 1024).toFixed(0)} KB</div>}
                </div>
              ))}
            </div>
          )
        )}
      </div>

      {/* I1: PDF Download + I2: Questionnaire shortcut */}
      <div className="flex gap-3 mt-5">
        <button onClick={downloadPdf} className="btn-outline text-sm flex items-center gap-1.5">
          <Download size={14} /> Download wealth summary PDF
        </button>
        <Link href={`/canvas/questionnaire${!isClient && hid ? `?household_id=${hid}` : ""}`}
              className="btn-outline text-sm flex items-center gap-1.5">
          <ClipboardList size={14} /> Risk questionnaire
        </Link>
      </div>

      {/* I3: Fee Transparency Card */}
      <div className="card p-5 mt-5">
        <div className="font-semibold text-ink mb-3 flex items-center justify-between">
          <span className="flex items-center gap-2"><DollarSign size={17} className="text-navy-600" /> Fee transparency</span>
          {!feesLoaded && <button className="text-xs text-ink-muted hover:text-ink underline" onClick={loadFees}>Load</button>}
        </div>
        {feesLoaded && fees && (
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-navy-50 rounded-xl p-3 text-center">
                <div className="text-lg font-bold text-ink">{fees.fee_pct ? `${(fees.fee_pct * 100).toFixed(2)}%` : "—"}</div>
                <div className="text-xs text-ink-muted">Annual fee rate</div>
              </div>
              <div className="bg-navy-50 rounded-xl p-3 text-center">
                <div className="text-lg font-bold text-ink">{fees.currency} {fees.annual_fee?.toLocaleString(undefined, {maximumFractionDigits:0}) || "—"}</div>
                <div className="text-xs text-ink-muted">Estimated annual fee</div>
              </div>
              <div className="bg-navy-50 rounded-xl p-3 text-center">
                <div className="text-lg font-bold text-ink">{fees.currency} {fees.ytd_fee?.toLocaleString(undefined, {maximumFractionDigits:0}) || "—"}</div>
                <div className="text-xs text-ink-muted">YTD fee (est.)</div>
              </div>
            </div>
            {fees.applicable_segment && (
              <div className="text-xs text-ink-muted">
                Segment: <span className="font-medium">{fees.applicable_segment.label}</span> · {fees.applicable_segment.fee_bps} bps on {fees.currency} {fees.aum?.toLocaleString(undefined, {maximumFractionDigits:0})} AUM
              </div>
            )}
            {fees.fee_schedule?.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-ink-muted hover:text-ink">View full fee schedule</summary>
                <table className="mt-2 w-full">
                  <thead><tr className="text-left text-ink-muted"><th className="pb-1">Segment</th><th>Min AUM</th><th>Rate</th></tr></thead>
                  <tbody>
                    {fees.fee_schedule.map((s: any) => (
                      <tr key={s.slug} className="border-t border-navy-100">
                        <td className="py-1 pr-4">{s.label}</td>
                        <td className="py-1 pr-4">{s.min_aum ? `${fees.currency} ${s.min_aum.toLocaleString()}` : "Any"}</td>
                        <td className="py-1">{(s.fee_pct * 100).toFixed(2)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
          </div>
        )}
        {feesLoaded && !fees && <div className="text-sm text-ink-muted">Fee information not available.</div>}
      </div>

      {/* Assistant */}
      <div className="card p-5 mt-5">
        <div className="font-semibold text-ink mb-1">Ask {view.adviser?.name?.split(" ")[0] || "your adviser"}’s assistant</div>
        <p className="text-xs text-ink-muted mb-3">Always-on, in {view.firm?.name}’s voice. For advice, it hands off to your named adviser.</p>
        <div className="space-y-3 mb-3">
          {thread.length === 0 && (
            <div>
              <p className="text-xs text-ink-muted mb-2">Try asking:</p>
              <div className="flex flex-wrap gap-2">
                {["Am I going to be okay?", "What happens if markets fall?", "Are my investments aligned to my values?"].map((s) => (
                  <button key={s} onClick={() => ask(s)} className="chip bg-navy-50 text-navy-700 hover:bg-navy-100 border border-navy-100">{s}</button>
                ))}
              </div>
            </div>
          )}
          {thread.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="bg-navy-800 text-white rounded-2xl rounded-br-sm px-4 py-2 max-w-[80%] text-sm">{m.text}</div>
              </div>
            ) : (
              <div key={i} className="bg-navy-50 rounded-2xl rounded-bl-sm px-4 py-3 max-w-[90%]">
                <p className={`text-sm whitespace-pre-line ${m.error ? "text-critical" : "text-ink-soft"}`}>{m.answer}</p>
                {!m.error && (
                  <button onClick={() => speak(m.answer)} className="text-xs text-ink-muted hover:text-navy-700 mt-1.5 flex items-center gap-1">
                    <Volume2 size={12} /> Listen
                  </button>
                )}
              </div>
            )
          )}
          {busy && <div className="text-sm text-ink-muted">Thinking…</div>}
        </div>
        <form onSubmit={(e) => { e.preventDefault(); ask(q); }} className="flex gap-2">
          <input className="input" placeholder="Ask anything about your plan…" value={q} onChange={(e) => setQ(e.target.value)} />
          {voiceSupported() && (
            <button type="button" onClick={startVoice} disabled={busy || listening}
                    className={`btn-outline ${listening ? "text-critical animate-pulse" : ""}`} title="Ask by voice">
              <Mic size={16} />
            </button>
          )}
          <button className="btn-primary" disabled={busy} aria-label="Send message"><Send size={16} /></button>
        </form>
        <div className="flex items-center gap-1.5 text-xs text-ink-muted mt-2">
          <ShieldCheck size={12} className="text-positive" /> Your data stays in {view.firm?.name}; it never trains external models.
        </div>
      </div>
    </div>
  );
}
