"use client";
import { useEffect, useRef, useState } from "react";
import { Eye, Radar, Cpu, Sparkles, Zap, Check, ShieldAlert, Undo2, Send, Activity } from "lucide-react";
import { api } from "@/lib/api";
import { timeAgo, titleCase } from "@/lib/format";

const META: Record<string, { icon: any; color: string }> = {
  watching: { icon: Eye, color: "text-ink-muted" },
  sensing: { icon: Radar, color: "text-navy-500" },
  thinking: { icon: Cpu, color: "text-navy-600" },
  proposed: { icon: Sparkles, color: "text-gold-dark" },
  acted: { icon: Zap, color: "text-[#6b4b78]" },
  decided: { icon: Check, color: "text-positive" },
  flagged: { icon: ShieldAlert, color: "text-caution" },
  rolled_back: { icon: Undo2, color: "text-critical" },
  scanned: { icon: Radar, color: "text-navy-500" },
  delegated: { icon: Send, color: "text-gold-dark" },
};

export default function ActivityRail() {
  const [events, setEvents] = useState<any[]>([]);
  const lastAt = useRef<string | null>(null);
  const [pulse, setPulse] = useState(false);

  async function poll() {
    try {
      const since = lastAt.current ? `?since=${encodeURIComponent(lastAt.current)}` : "?limit=30";
      const fresh = await api<any[]>(`/api/atlas/activity${since}`);
      if (fresh.length) {
        if (lastAt.current) { setPulse(true); setTimeout(() => setPulse(false), 1200); }
        setEvents((prev) => [...fresh, ...prev].slice(0, 60));
        lastAt.current = fresh[0].created_at;
      } else if (!lastAt.current && events.length === 0) {
        // nothing yet
      }
    } catch {}
  }

  useEffect(() => {
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="card p-0 overflow-hidden sticky top-7">
      <div className="px-4 py-3 border-b border-navy-100 flex items-center gap-2">
        <Activity size={16} className="text-navy-600" />
        <span className="font-semibold text-ink text-sm">Workforce activity</span>
        <span className={`ml-auto inline-flex items-center gap-1 text-[11px] ${pulse ? "text-positive" : "text-ink-muted"}`}>
          <span className={`h-1.5 w-1.5 rounded-full bg-positive ${pulse ? "animate-ping" : "animate-pulse"}`} /> live
        </span>
      </div>
      <div className="max-h-[70vh] overflow-y-auto divide-y divide-navy-50">
        {events.length === 0 && (
          <div className="px-4 py-8 text-center text-xs text-ink-muted">The workforce is on duty — activity will appear here.</div>
        )}
        {events.map((e) => {
          const m = META[e.kind] || META.watching;
          const Icon = m.icon;
          return (
            <div key={e.id} className="px-4 py-2.5 flex items-start gap-2.5 fade-in hover:bg-navy-50/40">
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
