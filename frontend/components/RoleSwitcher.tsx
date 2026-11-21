"use client";
import { useEffect, useState } from "react";
import { Repeat, Check } from "lucide-react";
import { api, getUser, login } from "@/lib/api";
import { roleLanding } from "@/lib/roles";

export default function RoleSwitcher() {
  const [open, setOpen] = useState(false);
  const [personas, setPersonas] = useState<any[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const current = typeof window !== "undefined" ? getUser() : null;

  useEffect(() => {
    // Stay inside the firm's market: switching role is not switching firm.
    const market = current?.firm?.jurisdiction === "IN" ? "IN" : "NZ";
    const slug = current?.firm?.slug;
    api<any[]>("/api/auth/demo-personas")
      .then((all) => {
        // A firm that carries its own people (a demonstration firm made at run time) switches among them only.
        const own = all.filter((p) => p.firm_slug && p.firm_slug === slug);
        setPersonas(own.length ? own : all.filter((p) => !p.firm_slug && (p.market || "NZ") === market));
      })
      .catch(() => setPersonas([]));
  }, []);

  if (!personas.length) return null;

  const groups: Record<string, any[]> = {};
  personas.forEach((p) => (groups[p.group] ||= []).push(p));

  async function pick(p: any) {
    setBusy(p.email);
    try {
      await login(p.email, "aurea");
      // Staff land in the conversation; admins and clients keep the page the persona catalogue names.
      window.location.href = ["admin", "superadmin", "client"].includes(p.role) ? p.default_path : roleLanding(p.role);
    } catch {
      setBusy(null);
    }
  }

  return (
    <div className="relative">
      <button
        data-testid="role-switcher"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-ink-soft transition-colors hover:bg-border-soft hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <Repeat size={16} className="shrink-0 text-ink-muted" aria-hidden /> Switch role
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full mb-2 left-0 w-72 max-w-[calc(100vw-2rem)] max-h-[70vh] overflow-y-auto bg-surface rounded-xl shadow-lift border border-border z-50 p-2">
            <div className="px-2 py-1.5 text-[10px] uppercase tracking-wide text-ink-muted">
              Switch role
            </div>
            {Object.entries(groups).map(([group, items]) => (
              <div key={group} className="mb-1">
                <div className="px-2 pt-1.5 pb-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{group}</div>
                {items.map((p) => {
                  const active = current?.email === p.email;
                  return (
                    <button
                      key={p.email}
                      data-testid="role-option"
                      data-role={p.role}
                      data-email={p.email}
                      onClick={() => pick(p)}
                      disabled={!!busy}
                      className={`w-full text-left px-2 py-1.5 rounded-lg flex items-start gap-2 hover:bg-border-soft transition ${active ? "bg-border-soft" : ""}`}
                    >
                      <span className="h-7 w-7 shrink-0 rounded-full bg-border text-ink flex items-center justify-center text-xs font-semibold mt-0.5">
                        {p.full_name.split(" ").map((x: string) => x[0]).slice(0, 2).join("")}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="text-sm font-medium text-ink flex items-center gap-1.5">
                          {p.title}{active && <Check size={12} className="text-ok" />}
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
