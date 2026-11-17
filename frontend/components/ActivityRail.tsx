"use client";
import { useEffect, useRef, useState } from "react";
import { Eye, Radar, Cpu, Sparkles, Zap, Check, ShieldAlert, Undo2, Send, Activity } from "lucide-react";
import { api } from "@/lib/api";
import { timeAgo, titleCase } from "@/lib/format";

const META: Record<string, { icon: any; color: string }> = {
  watching: { icon: Eye, color: "text-ink-muted" },
  sensing: { icon: Radar, color: "text-ink-muted" },
  thinking: { icon: Cpu, color: "text-ink-muted" },
  proposed: { icon: Sparkles, color: "text-info" },
  acted: { icon: Zap, color: "text-warn" },
  decided: { icon: Check, color: "text-ok" },
  flagged: { icon: ShieldAlert, color: "text-warn" },
  rolled_back: { icon: Undo2, color: "text-crit" },
  scanned: { icon: Radar, color: "text-ink-muted" },
  delegated: { icon: Send, color: "text-info" },
};

export default function ActivityRail() {
  const [events, setEvents] = useState<any[]>([]);
  const lastAt = useRef<string | null>(null);
  const [pulse, setPulse] = useState(false);

  // One read at a time: two first reads at once (React mounts an effect twice in development) each
  // brought the latest thirty, and every event was listed twice under one key (B-100).
  const inFlight = useRef(false);

  async function poll() {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const since = lastAt.current ? `?since=${encodeURIComponent(lastAt.current)}` : "?limit=30";
      const fresh = await api<any[]>(`/api/atlas/activity${since}`);
      if (fresh.length) {
        if (lastAt.current) { setPulse(true); setTimeout(() => setPulse(false), 1200); }
        // An event on the boundary of "since" can come back again: it is listed once.
        setEvents((prev) => {
          const seen = new Set(prev.map((e) => e.id));
          const added = fresh.filter((e) => !seen.has(e.id) && (seen.add(e.id), true));
          return added.length ? [...added, ...prev].slice(0, 60) : prev;
        });
        lastAt.current = fresh[0].created_at;
      }
    } catch {
    } finally {
      inFlight.current = false;
    }
  }

  useEffect(() => {
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="rounded-lg border border-border bg-surface overflow-hidden" data-testid="activity-rail">
      <div className="px-4 py-3 border-b border-border flex items-center gap-2">
        <Activity size={16} className="text-ink-muted" />
        <span className="font-semibold text-ink text-sm">Workforce activity</span>
        {/* Motion only for something arriving (rule 9): the dot is still until an event lands. */}
        <span className={`ml-auto inline-flex items-center gap-1 text-[11px] ${pulse ? "text-ok" : "text-ink-muted"}`}>
          <span className={`h-1.5 w-1.5 rounded-full bg-ok ${pulse ? "animate-ping motion-reduce:animate-none" : ""}`} /> live
        </span>
      </div>
      <div className="max-h-[70vh] overflow-y-auto divide-y divide-border-soft">
        {events.length === 0 && (
          <div className="px-4 py-8 text-center text-xs text-ink-muted">No activity yet</div>
        )}
        {events.map((e) => {
          const m = META[e.kind] || META.watching;
          const Icon = m.icon;
          return (
            <div key={e.id} className="px-4 py-2.5 flex items-start gap-2.5 fade-in hover:bg-border-soft">
              <Icon size={15} className={`mt-0.5 shrink-0 ${m.color}`} />
              <div className="min-w-0 flex-1">
                <div className="text-sm text-ink-soft leading-snug">{e.summary}</div>
                <div className="text-[11px] text-ink-muted mt-0.5">
                  {titleCase(e.kind)}{e.subject_label ? ` · ${e.subject_label}` : ""} · {timeAgo(e.created_at)}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
