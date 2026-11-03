"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowRight, Check, Circle, CircleDot, Leaf, Sprout } from "lucide-react";
import { Card, Empty, ErrorState, SkeletonList } from "@/components/ui";
import { api, getUser } from "@/lib/api";
import { titleCase } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { CanvasHouseholdPicker } from "./CanvasView";
import { AllClients, ViewHeader } from "./ViewHeader";

const ROUTE = "/canvas/next-gen";

/** A next-gen member's journey. The member takes its steps themselves; staff see where each member of a
 * household is, read only (the server refuses a step from staff). */
export function CanvasNextGenView({ household = null }: { household?: string | null }) {
  const isClient = getUser()?.role === "client";
  if (isClient) return <Journey path="/api/canvas/heir-journey" own />;
  return household ? <Members householdId={household} /> : <div className="view-container"><CanvasHouseholdPicker title="Next-gen journeys" route={ROUTE} testId="next-gen-households" /></div>;
}

function Members({ householdId }: { householdId: string }) {
  const path = `/api/canvas/me?household_id=${householdId}`;
  const { data, loading, error, refetch } = useApi<any>(path, [path]);
  const [chosen, setChosen] = useState<string | null>(null);
  if (error) return <div className="view-container"><Card><ErrorState what="the household" message={error} onRetry={refetch} /></Card></div>;
  if (loading || !data || data.household_id !== householdId) return <div className="view-container"><SkeletonList count={3} /></div>;
  const members: any[] = data.next_gen ?? [];
  const name: string = data.household?.name ?? "";
  const pid = members.find((m) => m.id === chosen)?.id ?? members[0]?.id ?? null;

  return (
    <div className="view-container" data-testid="canvas-next-gen-view">
      <ViewHeader
        title={`Next-gen · ${name}`}
        meta={<><span className="chip text-ink-muted">{members.length} next-gen</span><span className="chip text-ink-muted">read only</span></>}
        actions={<AllClients href={ROUTE} />}
      />
      {members.length === 0 ? <Card><Empty>No next-gen members on file</Empty></Card> : (
        <>
          {members.length > 1 && (
            <div className="mb-4 flex flex-wrap gap-1.5" data-testid="next-gen-members">
              {members.map((m: any) => (
                <button key={m.id} type="button" onClick={() => setChosen(m.id)} aria-pressed={m.id === pid}
                  className={`chip border ${m.id === pid ? "border-ink-muted bg-border-soft text-ink" : "border-border text-ink-muted hover:bg-border-soft"}`}>
                  {m.name}
                </button>
              ))}
            </div>
          )}
          {pid && <Journey key={pid} path={`/api/canvas/heir-journey?person_id=${pid}`} own={false} />}
        </>
      )}
    </div>
  );
}

/** One journey. `own` is the member's own sign-in: only then are the steps taken here. */
function Journey({ path, own }: { path: string; own: boolean }) {
  const { data: j, loading, error, refetch } = useApi<any>(path, [path]);
  const [valuesInput, setValuesInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [stepError, setStepError] = useState<string | null>(null);

  const wrap = (body: ReactNode) => (own ? <div className="view-container" data-testid="canvas-next-gen-view">{body}</div> : <>{body}</>);
  if (error) return wrap(<Card><ErrorState what="the journey" message={error} onRetry={refetch} /></Card>);
  if (loading || !j) return wrap(<SkeletonList count={4} />);

  const steps: any[] = j.steps ?? [];
  const activeIdx = steps.findIndex((s: any) => !s.done);
  const current = activeIdx === -1 ? steps.length - 1 : activeIdx;
  const accent: string | undefined = j.firm?.branding?.accent || undefined;
  const progress = Math.round((j.progress ?? 0) * 100);
  const done = steps.filter((s: any) => s.done).length;

  async function completeStep(key: string, captured?: any) {
    if (!own) return;
    setBusy(true);
    setStepError(null);
    try {
      await api(`/api/canvas/heir-journey/step`, { body: { key, captured } });
      await refetch();
    } catch (e: any) {
      setStepError(e?.message || "Could not save that step");
    } finally {
      setBusy(false);
    }
  }

  return wrap(
    <>
      {own && <ViewHeader title="My next-gen journey" meta={j.firm?.name ? <span className="chip text-ink-muted">{j.firm.name}</span> : undefined} />}

      <Card className="mb-4 border-l-4" style={accent ? { borderLeftColor: accent } : undefined} data-testid="next-gen-progress">
        <div className="flex flex-wrap items-center gap-3">
          <Sprout size={18} className="shrink-0 text-ok" aria-hidden />
          <span className="min-w-0 truncate text-sm font-medium text-ink">{j.person?.name}</span>
          <span className={`chip ${j.status === "completed" ? "bg-ok/10 text-ok" : "text-ink-muted"}`}>{titleCase(j.status ?? "")}</span>
          <span className="ml-auto font-mono text-lg tabular-nums text-ink">{progress}%</span>
        </div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-border-soft">
          <div className="h-full rounded-full bg-ok" style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} />
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <span className="chip text-ink-muted">{done} of {steps.length} steps</span>
          {j.adviser?.name && <span className="chip text-ink-muted">adviser {j.adviser.name}</span>}
        </div>
      </Card>

      {stepError && <div role="alert" className="mb-3 rounded-lg border border-crit/35 bg-crit-bg px-3 py-2 text-sm text-ink">{stepError}</div>}

      <div className="space-y-3" data-testid="next-gen-steps">
        {steps.map((s: any, i: number) => {
          const state = s.done ? "done" : i === current ? "active" : "todo";
          return (
            <Card key={s.key} className={state === "active" ? "border-ink-muted" : ""} data-testid="next-gen-step">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 shrink-0">
                  {state === "done" ? <Check className="text-ok" size={18} aria-label="done" />
                    : state === "active" ? <CircleDot className="text-ink" size={18} aria-label="next" />
                    : <Circle className="text-ink-faint" size={18} aria-label="to do" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium text-ink">{s.title}</span>
                    <span className={`chip ${state === "done" ? "text-ok" : state === "active" ? "bg-border-soft text-ink" : "text-ink-muted"}`}>
                      {state === "done" ? "done" : state === "active" ? "next" : "to do"}
                    </span>
                  </div>
                  {s.blurb && <div className="mt-1 text-xs text-ink-muted">{s.blurb}</div>}

                  {own && state === "active" && !s.done && (
                    <div className="mt-3">
                      {s.key === "values" ? (
                        <div className="flex flex-wrap gap-2 cq-sm:flex-nowrap">
                          <input className="input" aria-label="Values" placeholder="climate, education, community" value={valuesInput} onChange={(e) => setValuesInput(e.target.value)} />
                          <button className="btn-primary shrink-0" disabled={busy}
                            onClick={() => completeStep("values", { values_themes: valuesInput.split(",").map((x) => x.trim()).filter(Boolean) })}>Save</button>
                        </div>
                      ) : s.key === "connect" ? (
                        <Link href="/canvas" className="btn-primary" onClick={() => completeStep("connect")}>
                          Say hello to {j.adviser?.name?.split(" ")[0] || "your adviser"} <ArrowRight size={14} aria-hidden />
                        </Link>
                      ) : (
                        <button className="btn-primary" disabled={busy} onClick={() => completeStep(s.key)}>
                          {busy ? "Saving…" : "Continue"} <ArrowRight size={14} aria-hidden />
                        </button>
                      )}
                    </div>
                  )}

                  {s.done && s.key === "values" && j.captured?.values_themes?.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {j.captured.values_themes.map((t: string) => <span key={t} className="chip bg-ok/10 text-ok"><Leaf size={11} aria-hidden /> {titleCase(t)}</span>)}
                    </div>
                  )}
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}
