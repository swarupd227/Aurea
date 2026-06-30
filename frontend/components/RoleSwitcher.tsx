"use client";
import { useEffect, useState } from "react";
import { Repeat, Check } from "lucide-react";
import { api, getUser, login } from "@/lib/api";

export default function RoleSwitcher() {
  const [open, setOpen] = useState(false);
  const [personas, setPersonas] = useState<any[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const current = typeof window !== "undefined" ? getUser() : null;

  useEffect(() => {
    api("/api/auth/demo-personas").then(setPersonas).catch(() => setPersonas([]));
  }, []);

  if (!personas.length) return null;

  const groups: Record<string, any[]> = {};
  personas.forEach((p) => (groups[p.group] ||= []).push(p));

  async function pick(p: any) {
    setBusy(p.email);
    try {
      await login(p.email, "aurea");
      window.location.href = p.default_path;
    } catch {
      setBusy(null);
    }
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs text-navy-200/80 hover:bg-white/5 hover:text-white transition"
      >
        <Repeat size={14} /> Switch role
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full mb-2 left-0 w-72 max-h-[70vh] overflow-y-auto bg-surface rounded-xl shadow-lift border border-navy-100 z-50 p-2">
            <div className="px-2 py-1.5 text-[10px] uppercase tracking-wide text-ink-muted">
              Sign in as a persona · demo
            </div>
            {Object.entries(groups).map(([group, items]) => (
              <div key={group} className="mb-1">
                <div className="px-2 pt-1.5 pb-0.5 text-[10px] font-semibold uppercase tracking-wide text-navy-400">{group}</div>
                {items.map((p) => {
                  const active = current?.email === p.email;
                  return (
                    <button
                      key={p.email}
                      onClick={() => pick(p)}
                      disabled={!!busy}
                      className={`w-full text-left px-2 py-1.5 rounded-lg flex items-start gap-2 hover:bg-navy-50 transition ${active ? "bg-navy-50" : ""}`}
                    >
                      <span className="h-7 w-7 shrink-0 rounded-full bg-navy-800 text-white flex items-center justify-center text-xs font-semibold mt-0.5">
                        {p.full_name.split(" ").map((x: string) => x[0]).slice(0, 2).join("")}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="text-sm font-medium text-ink flex items-center gap-1.5">
                          {p.title}{active && <Check size={12} className="text-positive" />}
                        </span>
                        <span className="block text-[11px] text-ink-muted leading-tight">{p.description}</span>
                      </span>
                      {busy === p.email && <span className="text-[10px] text-ink-muted mt-1">…</span>}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
