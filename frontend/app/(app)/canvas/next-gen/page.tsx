"use client";
import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Sprout, Check, CircleDot, Circle, Heart, ArrowRight, Leaf } from "lucide-react";
import { Spinner } from "@/components/ui";
import { api, getUser } from "@/lib/api";
import { titleCase } from "@/lib/format";

export default function NextGenJourney() {
  const params = useSearchParams();
  const personId = params.get("person_id");
  const isClient = typeof window !== "undefined" ? getUser()?.role === "client" : false;
  const qp = !isClient && personId ? `?person_id=${personId}` : "";
  const [j, setJ] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [valuesInput, setValuesInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    try { setJ(await api(`/api/canvas/heir-journey${qp}`)); } finally { setLoading(false); }
  }
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  if (loading || !j) return <Spinner label="Loading your journey…" />;

  const accent = j.firm?.branding?.accent || "#c8a35e";
  const activeIdx = j.steps.findIndex((s: any) => !s.done);
  const current = activeIdx === -1 ? j.steps.length - 1 : activeIdx;

  async function completeStep(key: string, captured?: any) {
    setBusy(true);
    try { await api(`/api/canvas/heir-journey/step`, { body: { key, captured, person_id: !isClient ? personId : undefined } }); await load(); }
    finally { setBusy(false); }
  }

  return (
    <div className="max-w-3xl mx-auto">
      {/* Hero */}
      <div className="rounded-2xl p-8 text-white relative overflow-hidden mb-6" style={{ background: "#0f2b3d" }}>
        <div className="absolute inset-0 opacity-30" style={{ background: `radial-gradient(600px circle at 80% 20%, ${accent}55, transparent 60%)` }} />
        <div className="relative">
          <div className="flex items-center gap-2 text-sm text-white/60"><Sprout size={15} style={{ color: accent }} /> {j.firm?.name} · Next-gen</div>
          <h1 className="font-serif text-3xl mt-2">Welcome, {j.person?.name}</h1>
          <p className="text-white/70 mt-2 max-w-lg">Your family has built something meaningful. This is your space to understand it — on your own terms.</p>
          <div className="mt-4 flex items-center gap-3">
            <div className="h-2 w-48 rounded-full bg-white/15 overflow-hidden">
              <div className="h-full" style={{ width: `${j.progress * 100}%`, background: accent }} />
            </div>
            <span className="text-sm text-white/70">{Math.round(j.progress * 100)}% · {titleCase(j.status)}</span>
          </div>
        </div>
      </div>

      {/* Steps */}
      <div className="space-y-3">
        {j.steps.map((s: any, i: number) => {
          const state = s.done ? "done" : i === current ? "active" : "todo";
          return (
            <div key={s.key} className={`card p-5 ${state === "active" ? "border-gold/40 shadow-lift" : ""}`}>
              <div className="flex items-start gap-3">
                <div className="mt-0.5">
                  {state === "done" ? <Check className="text-positive" size={20} />
                    : state === "active" ? <CircleDot className="text-gold" size={20} />
                    : <Circle className="text-navy-200" size={20} />}
                </div>
                <div className="flex-1">
                  <div className="font-semibold text-ink">{s.title}</div>
                  <p className="text-sm text-ink-soft mt-1">{s.blurb}</p>

                  {state === "active" && (
                    <div className="mt-3">
                      {s.key === "values" ? (
                        <div className="flex gap-2">
                          <input className="input" placeholder="e.g. climate, education, community" value={valuesInput} onChange={(e) => setValuesInput(e.target.value)} />
                          <button className="btn-gold" disabled={busy} onClick={() => completeStep("values", { values_themes: valuesInput.split(",").map((x) => x.trim()).filter(Boolean) })}>Save</button>
                        </div>
                      ) : s.key === "connect" ? (
                        <Link href="/canvas" className="btn-gold" onClick={() => completeStep("connect")}>
                          <Heart size={15} /> Say hello to {j.adviser?.name?.split(" ")[0] || "your adviser"} <ArrowRight size={14} />
                        </Link>
                      ) : (
                        <button className="btn-gold" disabled={busy} onClick={() => completeStep(s.key)}>
                          {busy ? "Saving…" : "Continue"} <ArrowRight size={14} />
                        </button>
                      )}
                    </div>
                  )}

                  {s.done && s.key === "values" && j.captured?.values_themes?.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {j.captured.values_themes.map((t: string) => <span key={t} className="chip bg-positive/10 text-positive"><Leaf size={11} /> {titleCase(t)}</span>)}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {j.status === "completed" && (
        <div className="card p-5 mt-4 border-positive/30 text-center">
          <Sprout className="mx-auto text-positive mb-1" />
          <div className="font-semibold text-ink">You're all set, {j.person?.name}.</div>
          <p className="text-sm text-ink-muted mt-1">Your adviser is here whenever you have a question.</p>
        </div>
      )}
    </div>
  );
}
